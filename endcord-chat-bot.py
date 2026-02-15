import http.client
import json
import logging
import os
import queue
import shutil
import subprocess
import threading
import time

EXT_NAME = "LLM Chat Bot"
EXT_VERSION = "0.1.0"
EXT_ENDCORD_VERSION = "1.3.0"
EXT_DESCRIPTION = "An extension that turns discord bot into LLM chatbot through llama-cli"
EXT_SOURCE = "https://github.com/sparklost/endcord-chat-bot"
logger = logging.getLogger(__name__)

MAX_MSG_SIZE = 2000   # max by discord


class Extension:
    """Main extension class"""

    def __init__(self, app):
        self.app = app
        self.trigger_start = app.config.get("ext_chat_bot_trigger_start", "eb;")
        send_typing = bool(app.config.get("ext_chat_bot_send_typing", True))
        self.reply = bool(app.config.get("ext_chat_bot_reply", True))
        self.ping = bool(app.config.get("ext_chat_bot_reply_ping", True))
        self.max_typing = int(app.config.get("ext_chat_bot_max_typing", 120))
        self.limit_history = int(app.config.get("ext_chat_bot_limit_history", 20))
        self.limit_msg = int(app.config.get("ext_chat_bot_limit_msg_len", 1000))
        self.limit_msg = min(max(self.limit_msg, 10), MAX_MSG_SIZE)

        self.listen_channel = app.config.get("ext_chat_bot_listen_channel", [])
        self.listen_guilds = app.config.get("ext_chat_bot_listen_guilds", [])

        server_exe = app.config.get("ext_chat_bot_llama_server_executable", "llama-server")
        model_path = app.config.get("ext_chat_bot_llama_server_model_path", "")
        system_prompt = app.config.get("ext_chat_bot_llama_server_prompt", "You are a helpful assistant")
        server_threads = app.config.get("ext_chat_bot_llama_server_threads", None)
        self.server_host = app.config.get("ext_chat_bot_server_host", "localhost")
        self.server_port = int(app.config.get("ext_chat_bot_server_port", 42737))

        self.typing_channel_id = None
        self.typing_sent = int(time.time())
        self.history = {}
        self.message_send_queue = queue.Queue()

        self.run = True
        if not self.app.token.startswith("Bot"):
            logger.info("Not running on user accounts!")
            self.run = False
            return

        # start server
        if server_exe:
            server_exe = os.path.abspath(os.path.expanduser(server_exe))
        model_path = os.path.abspath(os.path.expanduser(model_path))
        if server_exe:
            if not shutil.which(server_exe):
                logger.error("llama-server executable path is invalid")
                self.run = False
                return
            if not os.path.exists(model_path):
                logger.error(f"LLM model could not be found at path {server_exe}")
                self.run = False
                return
            dir_path = os.path.dirname(server_exe)
            cmd = [server_exe, "-m", model_path, "-p", system_prompt, "--port", str(self.server_port), "--predict", str(self.limit_msg)]
            if server_threads:
                cmd.append("-t")
                cmd.append(server_threads)
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=dir_path,
            )
            time.sleep(1)   # let it try to start
            if proc.poll() is not None:
                _, stderr = proc.communicate()
                logger.error(stderr.decode())
                return
            logger.info(f"llama-server started on port {self.server_port}")
        else:
            logger.info(f"Server not started, assuming its running on {self.server_host}:{self.server_port}")

        # start helper threads
        if send_typing:
            threading.Thread(target=self.typing_sender, daemon=True).start()
        threading.Thread(target=self.worker, daemon=True).start()


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
        """Worker thread that takes message from queue and sends it to llama server then sends response to discord"""
        while self.run:
            try:
                guild_id, channel_id, message_id, content = self.message_send_queue.get()
                # send message to llama-server and get response
                self.typing_channel_id = channel_id
                self.typing_started = int(time.time())
                if channel_id not in self.history:
                    self.history[channel_id] = []
                self.history[channel_id].append({"role": "user", "content": content})
                if len(self.history[channel_id]) > self.limit_history:
                    self.history[channel_id].pop(0)
                payload = json.dumps({
                    "model": "model",
                    "messages": self.history[channel_id],
                    "stream": False,
                })
                try:
                    connection = http.client.HTTPConnection(self.server_host, self.server_port)
                    connection.request("POST", "/v1/chat/completions", body=payload, headers={"Content-Type": "application/json"})
                    response = connection.getresponse()
                    data = json.loads(response.read())
                    reply = data["choices"][0]["message"]["content"]
                    self.history[channel_id].append({"role": "assistant", "content": reply})
                    if len(self.history[channel_id]) > self.limit_history:
                        self.history[channel_id].pop(0)
                except Exception as e:
                    connection.close()
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
                    self.app.discord.send_message(
                        channel_id,
                        reply,
                    )

            except Exception:
                self.typing_channel_id = None
                self.typing_started = None


    def on_message_event(self, new_message):
        """Ran when message event is received"""
        if not self.run:
            return

        data = new_message["d"]
        if data["channel_id"] not in self.listen_channel and data["guild_id"] not in self.listen_guilds:
            return

        if new_message["op"] == "MESSAGE_CREATE" and data["user_id"] != self.app.my_id and data["user_id"] not in self.app.blocked:
            if not data["content"].startswith(self.trigger_start):
                return
            content = data["content"][len(self.trigger_start):].strip()
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
