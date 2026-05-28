import os
import requests
import json
import sys
from dotenv import load_dotenv

BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
load_dotenv(os.path.join(BASE_DIR, ".env"))
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def get_latest_msg():
    print("Checking for latest message...")
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
    r = requests.get(url, params={"limit": 1, "offset": -1}).json()
    if r.get("ok") and r.get("result"):
        msg = r["result"][0].get("message", {})
        text = msg.get("text")
        print(f"Found Message: {text}")
        return text
    print("No message found.")
    return None

if __name__ == "__main__":
    text = get_latest_msg()
    if text:
        sys.path.append(BASE_DIR)
        from telegram_listener import get_ai_decision, execute_and_reply
        func_name, args, error = get_ai_decision(text)
        print(f"AI Decision: {func_name}, {args}, {error}")
        if not error:
            execute_and_reply(func_name, args)
            print("Reply sent!")
