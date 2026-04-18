import os
import datetime
import argparse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# 권한 범위 설정
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/tasks'
]

# 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR, 'credentials.json')
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')

def get_service():
    """Google 서비스 객체를 생성하고 반환합니다."""
    creds = None
    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # 인증 과정 (최초 1회만 실행됨)
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_PATH, 'w') as token:
            token.write(creds.to_json())
    
    calendar_service = build('calendar', 'v3', credentials=creds)
    tasks_service = build('tasks', 'v1', credentials=creds)
    return calendar_service, tasks_service

def list_upcoming_events(max_results=5):
    """다가오는 캘린더 일정 조회"""
    try:
        calendar_service, _ = get_service()
        now = datetime.datetime.now(datetime.UTC).isoformat().replace('+00:00', 'Z')
        events_result = calendar_service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=max_results, singleEvents=True,
            orderBy='startTime').execute()
        events = events_result.get('items', [])
        return events
    except HttpError as error:
        print(f'[Error] 캘린더 조회 실패: {error}')
        return []

def list_tasks():
    """태스크 목록 조회"""
    try:
        _, tasks_service = get_service()
        results = tasks_service.tasks().list(tasklist='@default').execute()
        return results.get('items', [])
    except HttpError as error:
        print(f'[Error] 태스크 조회 실패: {error}')
        return []

def add_event(summary, start_time_str):
    """일정 추가 (형식: 2026-04-12T14:00:00)"""
    try:
        calendar_service, _ = get_service()
        start_dt = datetime.datetime.fromisoformat(start_time_str)
        end_dt = start_dt + datetime.timedelta(hours=1)
        event = {
            'summary': summary,
            'start': {'dateTime': f"{start_time_str}Z", 'timeZone': 'UTC'},
            'end': {'dateTime': f"{end_dt.isoformat()}Z", 'timeZone': 'UTC'},
        }
        event = calendar_service.events().insert(calendarId='primary', body=event).execute()
        print(f"[Success] 캘린더 일정 추가 완료: {event.get('summary')}")
    except Exception as e:
        print(f'[Error] 일정 추가 실패: {e}')

def add_task(title, due_iso=None):
    """태스크 추가"""
    try:
        _, tasks_service = get_service()
        body = {'title': title}
        if due_iso: body['due'] = due_iso
        result = tasks_service.tasks().insert(tasklist='@default', body=body).execute()
        print(f"[Success] 구글 태스크 추가 완료: {result.get('title')}")
    except Exception as e:
        print(f'[Error] 태스크 추가 실패: {e}')

def show_briefing():
    """오늘의 일정과 태스크 통합 요약 브리핑"""
    print("\n📅 [오늘의 브리핑]")
    print("-" * 30)
    
    print("\n[구글 캘린더 일정]")
    events = list_upcoming_events()
    if not events: print("  일정이 없습니다.")
    for e in events:
        start = e['start'].get('dateTime', e['start'].get('date'))
        print(f"  - {start[:16].replace('T', ' ')}: {e.get('summary')}")
    
    print("\n[남은 할 일]")
    tasks = list_tasks()
    pending = [t for t in tasks if t['status'] != 'completed']
    if not pending: print("  할 일이 없습니다. 축하합니다!")
    for t in pending:
        print(f"  [ ] {t.get('title')}")
    print("-" * 30 + "\n")

def main():
    parser = argparse.ArgumentParser(description="Google AI Assistant CLI Tool")
    parser.add_argument('--brief', action='store_true', help='오늘의 일정 요약 브리핑')
    parser.add_argument('--add-task', type=str, help='새로운 할 일 추가')
    parser.add_argument('--add-cal', type=str, help='새로운 캘린더 일정 추가 (제목)')
    parser.add_argument('--start', type=str, help='일정 시작 시간 (YYYY-MM-DDTHH:MM:SS)')
    
    args = parser.parse_args()

    if args.brief:
        show_briefing()
    elif args.add_task:
        add_task(args.add_task)
    elif args.add_cal and args.start:
        add_event(args.add_cal, args.start)
    else:
        # 인자가 없으면 기본으로 브리핑 표시
        show_briefing()

if __name__ == "__main__":
    main()
