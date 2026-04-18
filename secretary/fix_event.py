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

def update_event():
    creds = get_creds()
    service = build('calendar', 'v3', credentials=creds)
    
    # 1. 대상 이벤트 찾기 (2026-04-13 일자)
    time_min = "2026-04-13T00:00:00Z"
    time_max = "2026-04-13T23:59:59Z"
    
    events_result = service.events().list(
        calendarId='primary', timeMin=time_min, timeMax=time_max,
        singleEvents=True, orderBy='startTime').execute()
    events = events_result.get('items', [])

    target_event = None
    for event in events:
        if "고객 주간보고" in event.get('summary', ''):
            target_event = event
            break

    if not target_event:
        print("❌ 수정할 일정을 찾지 못했습니다.")
        return

    event_id = target_event['id']
    print(f"✅ 수정 대상 발견: {target_event['summary']} (ID: {event_id})")

    # 2. 시간 수정 (오후 2시 = 14:00 KST)
    # KST 기준: 2026-04-13T14:00:00+09:00
    new_start = "2026-04-13T14:00:00+09:00"
    new_end = "2026-04-13T15:00:00+09:00"

    updated_body = {
        'summary': '고객 주간보고',
        'start': {'dateTime': new_start, 'timeZone': 'Asia/Seoul'},
        'end': {'dateTime': new_end, 'timeZone': 'Asia/Seoul'},
    }

    service.events().patch(calendarId='primary', eventId=event_id, body=updated_body).execute()
    print(f"🚀 일정이 성공적으로 수정되었습니다: 14:00 (KST)")

if __name__ == "__main__":
    update_event()
