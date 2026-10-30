# kripto_bot/tools/telegram_setup.py
"""Find the chat id for Telegram notifications and send a test message.

1. Create a bot with @BotFather and put its token in .env as TELEGRAM_BOT_TOKEN.
2. Send any message to your bot in Telegram.
3. python -m kripto_bot telegram-setup  → lists the chats that wrote to the bot.
4. Put your chat id in .env as TELEGRAM_CHAT_ID and run the command again: it
   sends a test message.

Stop the supervisor first: it reads the bot's updates for the Telegram commands, and
only one program may do that at a time.
"""
import sys

from kripto_bot import config
from kripto_bot.app.notify import Telegram, TelegramError, telegram_call


def main():
    token = config.TELEGRAM_BOT_TOKEN
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN is not set in .env. Create a bot with @BotFather first.")
    try:
        me = telegram_call(token, "getMe")
        print(f"Bot: @{me.get('username')}")
        updates = telegram_call(token, "getUpdates")
    except TelegramError as e:
        sys.exit(f"Telegram error: {e}")
    chats = {}
    for u in updates:
        chat = (u.get("message") or u.get("edited_message") or u.get("channel_post") or {}).get("chat")
        if chat:
            name = chat.get("title") or " ".join(filter(None, [chat.get("first_name"), chat.get("last_name")]))
            chats[chat["id"]] = f"{name} ({chat.get('type')})"
    if chats:
        print("Chats that wrote to the bot recently:")
        for cid, name in chats.items():
            print(f"  TELEGRAM_CHAT_ID={cid}   # {name}")
    else:
        print("No messages found. Send any message to the bot in Telegram, then run this again.")
    if config.TELEGRAM_CHAT_ID:
        try:
            Telegram(token, config.TELEGRAM_CHAT_ID).send_now("✅ kripto-bot notifications are set up.")
            print(f"Test message sent to chat {config.TELEGRAM_CHAT_ID}.")
        except TelegramError as e:
            sys.exit(f"Test message failed: {e}")
    else:
        print("Then put the id in .env as TELEGRAM_CHAT_ID and run this again to send a test message.")


if __name__ == "__main__":
    main()
