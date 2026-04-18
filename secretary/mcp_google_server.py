import os
import datetime
import sqlite3
import glob
from zoneinfo import ZoneInfo
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from mcp.server.fastmcp import FastMCP

# 권한 범위 설정
SCOPES = [
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/tasks'
]

# 시간대 및 경로 설정
KST = ZoneInfo("Asia/Seoul")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(BASE_DIR, 'token.json')
HISTORY_DIR = "/root/gemini_history"
DB_DIR = os.path.join(HISTORY_DIR, "database")

# FastMCP 서버 인스턴스 생성
mcp = FastMCP("Google-Assistant")

def get_creds():
    """인증된 자격 증명을 가져옵니다."""
    if not os.path.exists(TOKEN_PATH):
        raise FileNotFoundError("token.json이 없습니다. 먼저 인증을 완료해주세요.")
    
    creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds

@mcp.tool()
def search_history(keyword: str) -> str:
    """과거 대화 내용에서 특정 키워드를 검색하여 관련 정보를 찾습니다. 
    사용자가 과거에 했던 말이나 약속을 기억해낼 때 유용합니다.
    """
    db_files = glob.glob(os.path.join(DB_DIR, "history_*.db"))
    db_files.sort(reverse=True)
    
    if not db_files:
        return "검색할 과거 대화 이력이 없습니다."

    results_text = f"🔍 '{keyword}' 검색 결과:\n"
    found_count = 0

    for db_path in db_files:
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            query = """
            SELECT s.timestamp, m.role, m.content 
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.content LIKE ?
            ORDER BY s.timestamp DESC, m.order_index ASC
            """
            cursor.execute(query, (f'%{keyword}%',))
            rows = cursor.fetchall()
            
            if rows:
                for timestamp, role, content in rows:
                    found_count += 1
                    snippet = content.replace("\n", " ").strip()
                    if len(snippet) > 200:
                        snippet = snippet[:197] + "..."
                    results_text += f"[{timestamp}] {role}: {snippet}\n"
            conn.close()
        except Exception as e:
            continue

    if found_count == 0:
        return f"'{keyword}'와 관련된 과거 기록을 찾지 못했습니다."
    
    return results_text + f"\n총 {found_count}개의 항목을 찾았습니다."

@mcp.tool()
def sync_current_session() -> str:
    """현재 세션의 대화 내용을 데이터베이스에 즉시 저장하여 '기억'으로 만듭니다.
    중요한 정보나 결정 사항이 있을 때 호출하면 나중에 검색할 수 있습니다.
    """
    try:
        # 외부 스크립트 실행하여 저장 처리 (기존 로직 활용)
        save_script = os.path.join(BASE_DIR, "save_history.py")
        os.system(f"python3 {save_script}")
        return "현재 세션이 성공적으로 기억 저장소에 동기화되었습니다."
    except Exception as e:
        return f"동기화 중 오류 발생: {str(e)}"

@mcp.tool()
def list_google_events(max_results: int = 5) -> str:
    """구글 캘린더에서 다가오는 일정을 조회합니다."""
    try:
        creds = get_creds()
        service = build('calendar', 'v3', credentials=creds)
        
        # 현재 시각을 KST로 가져와서 ISO 포맷으로 전송
        now = datetime.datetime.now(KST).isoformat()
        
        events_result = service.events().list(
            calendarId='primary', timeMin=now,
            maxResults=max_results, singleEvents=True,
            orderBy='startTime').execute()
        events = events_result.get('items', [])

        if not events:
            return "일정이 없습니다."

        result = "📅 [다가오는 일정 (KST)]\n"
        for i, event in enumerate(events):
            start_raw = event['start'].get('dateTime', event['start'].get('date'))
            
            # KST로 변환하여 표시
            if 'T' in start_raw:
                dt = datetime.datetime.fromisoformat(start_raw.replace('Z', '+00:00'))
                dt_kst = dt.astimezone(KST)
                start_str = dt_kst.strftime('%Y-%m-%d %H:%M')
            else:
                start_str = start_raw  # 종일 일정의 경우 날짜만 표시
                
            event_id = event.get('id')
            result += f"{i+1}. [{event_id}] {start_str}: {event.get('summary')}\n"
        return result
    except Exception as e:
        return f"오류 발생: {str(e)}"

@mcp.tool()
def delete_google_event(event_id: str) -> str:
    """구글 캘린더에서 특정 일정을 삭제합니다.
    event_id: list_google_events에서 확인한 일정의 고유 ID
    """
    try:
        creds = get_creds()
        service = build('calendar', 'v3', credentials=creds)
        
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        return f"성공적으로 일정이 삭제되었습니다. (ID: {event_id})"
    except Exception as e:
        return f"오류 발생: {str(e)}"

@mcp.tool()
def add_google_event(summary: str, start_time: str, description: str = None, recur_weekly: bool = False) -> str:
    """구글 캘린더에 새로운 일정을 추가합니다. 
    start_time 형식: 'YYYY-MM-DDTHH:MM:SS' (예: 2026-04-12T14:00:00) - KST 기준
    recur_weekly: True로 설정하면 매주 같은 요일, 같은 시간에 반복되는 일정으로 등록합니다.
    """
    try:
        creds = get_creds()
        service = build('calendar', 'v3', credentials=creds)
        
        start_dt = datetime.datetime.fromisoformat(start_time).replace(tzinfo=KST)
        end_dt = start_dt + datetime.timedelta(hours=1)
        
        event = {
            'summary': summary,
            'description': description,
            'start': {'dateTime': start_dt.isoformat(), 'timeZone': 'Asia/Seoul'},
            'end': {'dateTime': end_dt.isoformat(), 'timeZone': 'Asia/Seoul'},
        }

        # 반복 규칙 추가 (매주 반복)
        if recur_weekly:
            event['recurrence'] = ['RRULE:FREQ=WEEKLY']

        event = service.events().insert(calendarId='primary', body=event).execute()
        msg = f"성공적으로 일정이 추가되었습니다: {event.get('summary')}"
        if recur_weekly:
            msg += " (매주 반복 일정으로 설정됨)"
        return msg
    except Exception as e:
        return f"오류 발생: {str(e)}"

@mcp.tool()
def proactive_schedule_check() -> str:
    """캘린더를 분석하여 '주간보고'와 같은 정기 일정의 누락 여부를 확인하고,
    필요시 자동으로 다음 일정을 제안하거나 등록합니다.
    """
    try:
        creds = get_creds()
        service = build('calendar', 'v3', credentials=creds)
        
        # 1. '주간보고' 키워드로 최근 일정 검색
        now = datetime.datetime.now(KST).isoformat()
        events_result = service.events().list(
            calendarId='primary', timeMin=now, q='주간보고',
            singleEvents=True, orderBy='startTime').execute()
        events = events_result.get('items', [])

        if not events:
            return "분석할 만한 정기 일정이 발견되지 않았습니다."

        # 2. 패턴 분석 (예: 월요일 14:00)
        latest_event = events[0]
        summary = latest_event.get('summary')
        start_raw = latest_event['start'].get('dateTime')
        if not start_raw: return "종일 일정은 패턴 분석에서 제외됩니다."
        
        dt = datetime.datetime.fromisoformat(start_raw.replace('Z', '+00:00')).astimezone(KST)
        next_week_dt = dt + datetime.timedelta(weeks=1)
        
        # 3. 다음 주 같은 시간에 일정이 있는지 확인
        time_min = next_week_dt.replace(hour=0, minute=0).isoformat()
        time_max = next_week_dt.replace(hour=23, minute=59).isoformat()
        
        check_result = service.events().list(
            calendarId='primary', timeMin=time_min, timeMax=time_max,
            q=summary).execute()
            
        if not check_result.get('items'):
            # 일정이 없으면 자동으로 추가 시도 (프로액티브!)
            new_start = next_week_dt.isoformat()
            add_google_event(summary, new_start, description="비서가 자동으로 생성한 정기 일정입니다.")
            return f"📢 감지된 패턴: 매주 {dt.strftime('%A')} {dt.strftime('%H:%M')}\n' {summary}' 일정이 다음 주에 없어 자동으로 등록했습니다: {next_week_dt.strftime('%Y-%m-%d %H:%M')}"
        
        return f"모든 '{summary}' 일정이 정상적으로 등록되어 있습니다."
    except Exception as e:
        return f"분석 중 오류: {str(e)}"

@mcp.tool()
def list_google_tasks() -> str:
    """구글 태스크에서 현재 할 일 목록을 조회합니다."""
    try:
        creds = get_creds()
        service = build('tasks', 'v1', credentials=creds)
        
        results = service.tasks().list(tasklist='@default').execute()
        tasks = results.get('items', [])

        if not tasks:
            return "등록된 할 일이 없습니다."

        result = "✅ [현재 할 일 목록]\n"
        for task in tasks:
            status = "[v]" if task['status'] == 'completed' else "[ ]"
            result += f"{status} {task.get('title')}\n"
        return result
    except Exception as e:
        return f"오류 발생: {str(e)}"

@mcp.tool()
def add_google_task(title: str, notes: str = None) -> str:
    """구글 태스크에 새로운 할 일을 추가합니다."""
    try:
        creds = get_creds()
        service = build('tasks', 'v1', credentials=creds)
        
        task_body = {'title': title, 'notes': notes}
        result = service.tasks().insert(tasklist='@default', body=task_body).execute()
        return f"성공적으로 할 일이 추가되었습니다: {result.get('title')}"
    except Exception as e:
        return f"오류 발생: {str(e)}"

if __name__ == "__main__":
    mcp.run()
