import os
import requests
import json
from datetime import datetime
from dotenv import load_dotenv

load_dotenv("workspace_py/stock_trader/.env")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

def test_ai():
    print(f"Testing Gemini API with Key: {GEMINI_API_KEY[:10]}...")
    # v1 엔드포인트 + gemini-pro (가장 범용적인 조합)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro-latest:generateContent?key={GEMINI_API_KEY}"
    
    prompt = "안녕? 너는 누구니? 간단하게 대답해줘."
    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    
    try:
        res = requests.post(url, json=payload, timeout=20)
        print(f"Status: {res.status_code}")
        print(f"Response: {res.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_ai()
