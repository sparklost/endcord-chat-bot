import http.client
import json
import logging
import queue
import threading
import time

EXT_NAME = "LLM Chat Bot"
EXT_VERSION = "0.3.0"
EXT_ENDCORD_VERSION = "1.5.0"
EXT_DESCRIPTION = "An extension that turns discord bot into LLM chatbot through ollama or llama-server"
EXT_SOURCE = "https://github.com/sparklost/endcord-chat-bot"
logger = logging.getLogger(__name__)

MAX_MSG_SIZE = 2000   # max by discord


class Extension:
    """Main extension class"""

    def __init__(self, app):
        self.app = app
        self.trigger = app.config.get("ext_chat_bot_trigger", "@me")
        send_typing = bool(app.config.get("ext_chat_bot_send_typing", True))
        self.reply = bool(app.config.get("ext_chat_bot_reply", True))
        self.ping = bool(app.config.get("ext_chat_bot_reply_ping", True))
        self.max_typing = int(app.config.get("ext_chat_bot_max_typing", 120))
        self.limit_history = int(app.config.get("ext_chat_bot_limit_history", 20))
        self.limit_msg = int(app.config.get("ext_chat_bot_limit_msg_len", 1000))
        self.limit_msg = min(max(self.limit_msg, 10), MAX_MSG_SIZE)

        self.listen_channels = app.config.get("ext_chat_bot_listen_channels", [])
        self.listen_guilds = app.config.get("ext_chat_bot_listen_guilds", [])

        self.backend = app.config.get("ext_chat_bot_backend", "ollama").lower()
        self.model = app.config.get("ext_chat_bot_model", "model")
        self.system_prompt = app.config.get("ext_chat_bot_system_prompt", "You are a helpful assistant")
        default_port = 11434 if self.backend == "ollama" else 8080
        self.server_host = app.config.get("ext_chat_bot_server_host", "localhost")
        self.server_port = int(app.config.get("ext_chat_bot_server_port", default_port))

        self.typing_channel_id = None
        self.typing_sent = int(time.time())
        self.history = {}
        self.message_send_queue = queue.Queue()

        self.run = True
        if not self.app.token.startswith("Bot"):
            logger.info("Not running on user accounts!")
            self.run = False
            del (type(self).on_message_event, type(self).on_message_event_is_irrelevant, type(self).on_main_start)
            return

        logger.info(f"Connecting to {self.backend} at {self.server_host}:{self.server_port}")

        # start helper threads
        if send_typing:
            threading.Thread(target=self.typing_sender, daemon=True).start()
        threading.Thread(target=self.worker, daemon=True).start()


    def on_main_start(self):
        """At this point there is app.my_id so update trigger"""
        if self.trigger.startswith("@me"):
            self.trigger = self.trigger[3:] + f"<@{self.app.my_id}>"


    def typing_sender(self):
        """Thread that sends typing status"""
        while self.run:
            if self.typing_channel_id and time.time() >= self.typing_sent + 7:
                if int(time.time()) > self.typing_started + self.max_typing:
                    self.typing_channel_id = None
                self.typing_sent = int(time.time())
                self.app.discord.send_typing(self.typing_channel_id)
            else:
                time.sleep(0.1)


    def worker(self):
        """Worker thread that takes message from queue, sends it to the LLM backend, and replies to discord"""
        while self.run:
            try:
                guild_id, channel_id, message_id, content = self.message_send_queue.get()
                self.typing_channel_id = channel_id
                self.typing_started = int(time.time())
                if channel_id not in self.history:
                    self.history[channel_id] = []
                self.history[channel_id].append({"role": "user", "content": content})
                if len(self.history[channel_id]) > self.limit_history:
                    self.history[channel_id].pop(0)

                # prepare payload
                messages_payload = []
                if self.system_prompt:
                    messages_payload.append({"role": "system", "content": self.system_prompt})
                messages_payload.extend(self.history[channel_id])
                if self.backend == "ollama":
                    endpoint = "/api/chat"
                    payload = json.dumps({
                        "model": self.model,
                        "messages": messages_payload,
                        "options": {
                            "num_predict": self.limit_msg,
                        },
                        "stream": False,
                    })
                else:
                    endpoint = "/v1/chat/completions"
                    payload = json.dumps({
                        "model": self.model,
                        "messages": messages_payload,
                        "max_tokens": self.limit_msg,
                        "stream": False,
                    })

                # get response
                try:
                    connection = http.client.HTTPConnection(self.server_host, self.server_port)
                    connection.request("POST", endpoint, body=payload, headers={"Content-Type": "application/json"})
                    response = connection.getresponse()
                    data = json.loads(response.read())
                    if self.backend == "ollama":
                        reply = data.get("message", {}).get("content", "")
                    else:
                        reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                    if reply:
                        self.history[channel_id].append({"role": "assistant", "content": reply})
                        if len(self.history[channel_id]) > self.limit_history:
                            self.history[channel_id].pop(0)
                    else:
                        reply = "Error: Received empty response from the server."
                    connection.close()
                except Exception as e:
                    reply = f"Internal server error! {e}"

                # send message to discord
                self.typing_channel_id = None
                self.typing_started = None
                reply = reply[:self.limit_msg - 1]   # failsafe
                if not reply:
                    continue
                if self.reply:
                    self.app.discord.send_message(
                        channel_id,
                        reply,
                        reply_id=message_id,
                        reply_channel_id=channel_id,
                        reply_guild_id=guild_id,
                        reply_ping=self.ping,
                    )
                else:
                    self.app.discord.send_message(channel_id, reply)

            except Exception:
                self.typing_channel_id = None
                self.typing_started = None


    def on_message_event_is_irrelevant(self, message, optext):
        """Check if message is relevant or not"""
        if optext != "MESSAGE_CREATE":
            return False
        if message["content"].startswith(self.trigger):
            return True


    def on_message_event(self, new_message):
        """Ran when message event is received"""
        data = new_message["d"]
        if data["channel_id"] not in self.listen_channels and data["guild_id"] not in self.listen_guilds:
            return

        if new_message["op"] == "MESSAGE_CREATE" and data["user_id"] != self.app.my_id and data["user_id"] not in self.app.blocked:
            if not data["content"].startswith(self.trigger):
                return
            content = data["content"][len(self.trigger):].strip()
            if not content:
                return

            guild_id = data["guild_id"]
            channel_id = data["channel_id"]

            if logger.getEffectiveLevel() == logging.DEBUG:
                body = ""
                if guild_id:
                    for guild in self.app.guilds:
                        if guild["guild_id"] == guild_id:
                            body += f"[{guild["name"]}] "
                            break
                    for channel in guild["channels"]:
                        if channel["id"] == channel_id and channel.get("permitted"):
                            body += f"#{channel["name"]} - "
                            break
                body += f"{data.get("username")} used: {data["content"]}"
                logger.debug(body)

            self.message_send_queue.put((guild_id, channel_id, data["id"], content))
