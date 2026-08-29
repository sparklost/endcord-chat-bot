import http.client
import json
import logging
import queue
import re
import threading
import time

EXT_NAME = "LLM Chat Bot"
EXT_VERSION = "0.3.1"
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
        self.limit_history = int(app.config.get("ext_chat_bot_limit_history", 1500))
        self.history_time = int(app.config.get("ext_chat_bot_history_time", 30)) * 60
        self.limit_msg = int(app.config.get("ext_chat_bot_limit_msg_len", 1000))
        self.limit_msg = min(max(self.limit_msg, 10), MAX_MSG_SIZE)
        self.usernames = app.config.get("ext_chat_bot_usernames", False)

        self.listen_channels = app.config.get("ext_chat_bot_listen_channels", [])
        self.listen_guilds = app.config.get("ext_chat_bot_listen_guilds", [])

        self.server_host = app.config.get("ext_chat_bot_server_host", "localhost")
        self.server_port = int(app.config.get("ext_chat_bot_server_port", 11434))
        self.openrouter_token = app.config.get("ext_chat_bot_openrouter_token", None)

        self.model = app.config.get("ext_chat_bot_model", None)
        self.system_prompt = app.config.get("ext_chat_bot_system_prompt", "You are a helpful assistant")
        self.temp = app.config.get("ext_chat_bot_llm_temp", 0.9)
        self.top_p = app.config.get("ext_chat_bot_llm_top_p", 1.0)
        self.repeat_penalty = app.config.get("ext_chat_bot_llm_repeat_penalty", 0.2)

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

        logger.info(f"Connecting to {self.server_host}:{self.server_port}")

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


    def query_llm(self, messages):
        """OpenAI-compatible inference for Ollama, llama-server, and OpenRouter"""
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.limit_msg,
            "temperature": self.temp,
            "top_p": self.top_p,
            "repetition_penalty": self.repeat_penalty,
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if self.openrouter_token:
            host = "openrouter.ai"
            port = 443
            path = "/api/v1/chat/completions"
            headers["Authorization"] = f"Bearer {self.openrouter_token}"
            use_https = True
        else:
            host = self.server_host
            port = self.server_port
            path = "/v1/chat/completions"
            use_https = False

        try:
            if use_https:
                conn = http.client.HTTPSConnection(host, port)
            else:
                conn = http.client.HTTPConnection(host, port)
            conn.request("POST", path, body=json.dumps(payload), headers=headers)
            res = conn.getresponse()
            res_body = res.read().decode("utf-8")
            conn.close()
            data = json.loads(res_body)
            if "choices" in data and len(data["choices"]) > 0:
                reply = data["choices"][0].get("message", {}).get("content", "")
                return reply if reply else "Error: Received empty string."
            logger.error(f"Unexpected response structure: {data}")
            return "Error: Unexpected API response format."
        except Exception as e:
            logger.error(f"LLM query failed: {e}")
            return f"Internal server error! {e}"


    def worker(self):
        """Worker thread that takes message from queue, sends it to the LLM backend, and replies to discord"""
        while self.run:
            try:
                guild_id, channel_id, message_id, content, username, ref_msg = self.message_send_queue.get()
                self.typing_channel_id = channel_id
                self.typing_started = int(time.time())
                if channel_id not in self.history:
                    self.history[channel_id] = []
                message_entry = {"role": "user", "content": content, "time": int(time.time())}
                if self.usernames:
                    message_entry["name"] = re.sub(r"[^a-zA-Z0-9_-]", "", username)[:64]
                self.history[channel_id].append(message_entry)

                # clean old messages
                if self.history_time:
                    self.history[channel_id] = [msg for msg in self.history[channel_id] if msg["time"] >= (int(time.time()) - self.history_time)]

                # handle reply
                history = [{"role": msg["role"], "content": msg["content"]} for msg in self.history[channel_id]]
                if ref_msg:
                    ref_text = ref_msg["content"]
                    for i, message in enumerate(history):
                        if message["role"] == "assistant" and message["content"] == ref_text:
                            history.pop(i)
                    reply_payload = {"role": "assitant", "content": f"User is replying to this content that I wrote:\n{ref_text}"}
                    history.append(reply_payload)

                # limit history
                while len(self.history[channel_id]) > 1 and sum(len(msg["content"]) for msg in self.history[channel_id]) > self.limit_history:
                    self.history[channel_id].pop(0)

                # prepare payload
                messages_payload = []
                if self.system_prompt:
                    messages_payload.insert(0, {"role": "system", "content": self.system_prompt})
                messages_payload.extend(history)
                reply = self.query_llm(messages_payload)
                self.history[channel_id].append({"role": "assistant", "content": reply, "time": int(time.time())})

                # send message to discord
                self.typing_channel_id = None
                self.typing_started = None
                if reply.lower().startswith("assistant\n\n"):
                    reply = reply[11:]
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
        ref_msg = message.get("referenced_message")
        if ref_msg and ref_msg["author"]["id"] == self.app.my_id:
            return True


    def on_message_event(self, new_message):
        """Ran when message event is received"""
        data = new_message["d"]
        if data["channel_id"] not in self.listen_channels and data["guild_id"] not in self.listen_guilds:
            return

        if new_message["op"] == "MESSAGE_CREATE" and data["user_id"] != self.app.my_id and data["user_id"] not in self.app.blocked:
            ref_msg = data.get("referenced_message")
            content = data["content"]
            if ref_msg and ref_msg["user_id"] != self.app.my_id:
                ref_msg = None
            if data["content"].startswith(self.trigger):
                content = content[len(self.trigger):].strip()
            elif not ref_msg:
                return

            guild_id = data["guild_id"]
            channel_id = data["channel_id"]
            if data.get("nick"):
                name = data["nick"]
            elif data.get("global_name"):
                name = data["global_name"]
            else:
                name = data["username"]

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

            self.message_send_queue.put((guild_id, channel_id, data["id"], content, name, ref_msg))
