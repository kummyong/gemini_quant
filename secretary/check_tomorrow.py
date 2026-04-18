import os
import datetime
from zoneinfo import ZoneInfo
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

KST = ZoneInfo("Asia/Seoul")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')
SCOPES = ['https://www.googleapis.com/auth/calendar']

def get_creds():
    if not os.path.exists(TOKEN_PATH):
        raise FileNotFoundError("token.json이 없습니다.")
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

def list_tomorrow_events():
    creds = get_creds()
    service = build('calendar', 'v3', credentials=creds)
    
    # 오늘이 2026-04-12이므로 내일은 2026-04-13입니다.
    # 정확하게 "내일"의 00:00:00부터 23:59:59까지 조회합니다.
    now = datetime.datetime.now(KST)
    tomorrow = now + datetime.timedelta(days=1)
    time_min = tomorrow.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    time_max = tomorrow.replace(hour=23, minute=59, second=59, microsecond=999999).isoformat()
    
    print(f"🔍 2026년 4월 13일(내일)의 일정을 조회합니다... ({time_min} ~ {time_max})")
    
    events_result = service.events().list(
        calendarId='primary', timeMin=time_min, timeMax=time_max,
        singleEvents=True, orderBy='startTime').execute()
    events = events_result.get('items', [])

    if not events:
        print("내일은 예정된 일정이 없습니다.")
        return

    print("\n📅 [내일의 일정]")
    for i, event in enumerate(events):
        start_raw = event['start'].get('dateTime', event['start'].get('date'))
        if 'T' in start_raw:
            dt = datetime.datetime.fromisoformat(start_raw.replace('Z', '+00:00'))
            dt_kst = dt.astimezone(KST)
            start_str = dt_kst.strftime('%H:%M')
        else:
            start_str = "종일"
        print(f"{i+1}. {start_str}: {event.get('summary')}")

if __name__ == "__main__":
    list_tomorrow_events()
