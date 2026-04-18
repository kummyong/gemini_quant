import os
import datetime
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_creds():
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

def check_recurring():
    creds = get_creds()
    service = build('calendar', 'v3', credentials=creds)
    
    # 2026-04-12부터 한 달간의 일정을 조회하여 '주간보고'가 언제 나타나는지 확인
    time_min = "2026-04-12T00:00:00Z"
    time_max = "2026-05-12T23:59:59Z"
    
    events_result = service.events().list(
        calendarId='primary', timeMin=time_min, timeMax=time_max,
        singleEvents=True, orderBy='startTime').execute()
    events = events_result.get('items', [])

    found_dates = []
    for event in events:
        if "주간보고" in event.get('summary', ''):
            start = event['start'].get('dateTime', event['start'].get('date'))
            dt = datetime.datetime.fromisoformat(start.replace('Z', '+00:00'))
            # 요일 한글로 변환
            days = ["월", "화", "수", "목", "금", "토", "일"]
            found_dates.append(f"{dt.strftime('%Y-%m-%d')} ({days[dt.weekday()]})")

    if found_dates:
        print(f"✅ '주간보고' 일정이 발견된 날짜들:")
        for d in found_dates:
            print(f"  - {d}")
    else:
        print("❌ '주간보고' 관련 일정을 찾지 못했습니다.")

if __name__ == "__main__":
    check_recurring()
