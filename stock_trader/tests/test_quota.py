import os
import requests
import json
import sys
from datetime import datetime
from dotenv import load_dotenv

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "..", ".."))
BASE_DIR = os.path.join(project_root, "stock_trader")
load_dotenv(os.path.join(BASE_DIR, ".env"))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def check_quota():
    if not GEMINI_API_KEY:
        print("GEMINI_API_KEY is not set. Skipping check.")
        return
    print(f"Checking Quota for Key: {GEMINI_API_KEY[:10]}...")
    # gemini-2.0-flash 모델로 테스트
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": "Quota test. Reply with 'OK'."}]}]}
    
    res = requests.post(url, json=payload, timeout=20)
    print(f"Status Code: {res.status_code}")
    if res.status_code != 200:
        print(f"Response: {res.text}")
    else:
        print("Quota is OK! API is working.")

if __name__ == "__main__":
    check_quota()
