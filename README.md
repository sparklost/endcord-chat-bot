# endcord-chat-bot
An extension for [endcord](https://github.com/sparklost/endcord) discord TUI client, that implements LLM-powered chatbot using llama-server or ollama.  
This extension is intended **for bots only**.  

## Installing
See [official extensions documentation](https://github.com/sparklost/endcord/blob/main/docs/extensions.md#installing-extensions) for installing instructions.
Available options:
- Git clone into `Extensions` directory located in endcord config directory.
- Run `endcord -i https://github.com/sparklost/endcord-chat-bot`
- Or use endcord client-side command `install_extension sparklost/endcord-chat-bot`

## Configuration
All extension options are under `[main]` section in endcord config. This extension options are always prefixed with `ext_chat_bot_`.  
Either ollama or llama-server is required to be running at configured address.

### Settings options
- `ext_chat_bot_trigger_start = "@me"`  
    Trigger string for chatbot features. Messages starting with this string will be sent to LLM.  
    `@me` is special token meaning this bot mention at the start of the message.
- `ext_chat_bot_send_typing = True`  
    Whether to send "typing..." status to current chat.
- `ext_chat_bot_max_typing = 120`  
    Longest period of time the bot can be sending "typing..." status. Value is in seconds.
- `ext_chat_bot_reply = True`  
    Whether to send response as a reply.
- `ext_chat_bot_reply_ping = True`  
    Whether to send reply with a ping.
- `ext_chat_bot_limit_history = 20`  
    Limit to the chat history sent to the LLM. History includes user and llm messages.  
    Larger history will slow down reply generation. Histories are stored per-channel.
- `ext_chat_bot_limit_msg_len = 1000`  
    Limit to the message size in characters. Hard limit is 2000 as that's how much discord allows. Will affect response generation speed.
- `ext_chat_bot_listen_channel = []`  
    List of channel IDs where to monitor messages. IDs must be strings (`"12345"`).
- `ext_chat_bot_listen_guilds = []`  
    List of server IDs where to monitor messages. IDs must be strings (`"12345"`).
    Overrides `ext_notify_mention_listen_channel` if channels are from same server.
- `ext_chat_bot_backend = "ollama"`  
    What backend to use, can be: `ollama` and `llama-server`.
- `ext_chat_bot_model = "model"`  
    What LLM model to use, it mus be installed in the backend.
- `ext_chat_bot_system_prompt = "You are a helpful assistant"`  
    System prompt for LLM.
- `ext_chat_bot_server_host = "localhost"`  
    Address at which server is running.
- `ext_chat_bot_server_port = 11434`  
    Port at which server is running.

## Disclaimer
> [!WARNING]
> Using third-party client is against Discord's Terms of Service and may cause your account to be banned!  
> **Use endcord and/or this extension at your own risk!**  
> If this extension is modified, it may be used for harmful or unintended purposes.  
> **The developer is not responsible for any misuse or for actions taken by users.**  
