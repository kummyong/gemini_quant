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

def debug_event_time():
    creds = get_creds()
    service = build('calendar', 'v3', credentials=creds)
    
    # 2026-04-13 일정을 다시 조회
    time_min = "2026-04-13T00:00:00Z"
    time_max = "2026-04-13T23:59:59Z"
    
    events_result = service.events().list(
        calendarId='primary', timeMin=time_min, timeMax=time_max,
        singleEvents=True, orderBy='startTime').execute()
    events = events_result.get('items', [])

    for event in events:
        summary = event.get('summary')
        start = event['start']
        print(f"--- Event: {summary} ---")
        print(f"Raw Start Data: {start}")

if __name__ == "__main__":
    debug_event_time()
