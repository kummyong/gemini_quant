import json
import os
import glob
import sqlite3
import sys
from datetime import datetime

# 설정: 이력이 저장될 폴더
HISTORY_DIR = "/root/gemini_history"
MD_DIR = os.path.join(HISTORY_DIR, "markdown")
DB_DIR = os.path.join(HISTORY_DIR, "database")

# 설정: Gemini 로그가 저장되는 상위 폴더
LOG_BASE_DIR = "/root/.gemini/tmp"
CONTEXT_PATH = "/root/last_session_context.txt"

def get_latest_db():
    # 가장 최근의 history_*.db 파일을 찾음
    db_files = glob.glob(os.path.join(DB_DIR, "history_*.db"))
    if not db_files:
        return None
    db_files.sort(reverse=True)
    return db_files[0]

def restore_context_from_db():
    db_path = get_latest_db()
    if not db_path:
        return False
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        # 가장 최근 세션의 요약 정보 가져오기
        cursor.execute("SELECT summary FROM sessions ORDER BY timestamp DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        
        if row and row[0]:
            with open(CONTEXT_PATH, 'w', encoding='utf-8') as cf:
                cf.write(row[0])
            print(f"[System] DB({os.path.basename(db_path)})에서 문맥을 성공적으로 복구했습니다.")
            return True
    except Exception as e:
        print(f"[System] 문맥 복구 중 오류: {e}")
    return False

def init_db_schema(conn):
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, session_file TEXT UNIQUE, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, summary TEXT)")
    cursor.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER, role TEXT, content TEXT, order_index INTEGER, FOREIGN KEY (session_id) REFERENCES sessions (id))")
    conn.commit()

def save_to_db(latest_log, messages, summary):
    try:
        if not os.path.exists(DB_DIR): os.makedirs(DB_DIR)
        current_month = datetime.now().strftime("%Y%m")
        db_path = os.path.join(DB_DIR, f"history_{current_month}.db")
        conn = sqlite3.connect(db_path)
        init_db_schema(conn)
        cursor = conn.cursor()
        session_file = os.path.basename(latest_log)
        
        # Upsert: 세션 파일이 이미 존재하면 summary와 timestamp를 업데이트
        cursor.execute("""
            INSERT INTO sessions (session_file, summary) VALUES (?, ?)
            ON CONFLICT(session_file) DO UPDATE SET 
                summary=excluded.summary,
                timestamp=CURRENT_TIMESTAMP
        """, (session_file, summary))
        
        cursor.execute("SELECT id FROM sessions WHERE session_file = ?", (session_file,))
        session_id = cursor.fetchone()[0]
        
        # 메시지는 항상 최신 상태로 갱신 (전체 삭제 후 재삽입)
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        for i, entry in enumerate(messages):
            role = entry.get('type', 'Unknown').capitalize()
            content_raw = entry.get('content', '')
            content = "\n".join([item.get('text', '') for item in content_raw if isinstance(item, dict) and 'text' in item]) if isinstance(content_raw, list) else content_raw
            if content.strip():
                cursor.execute("INSERT INTO messages (session_id, role, content, order_index) VALUES (?, ?, ?, ?)", (session_id, role, content, i))
        
        conn.commit()
        conn.close()
        print(f"[System] DB 동기화 완료: {os.path.basename(db_path)}")
    except Exception as e:
        print(f"[System] DB 저장 중 오류: {e}")

def save_latest_session():
    if not os.path.exists(HISTORY_DIR): os.makedirs(HISTORY_DIR)
    log_files = glob.glob(os.path.join(LOG_BASE_DIR, "**/chats/session-*.json"), recursive=True)
    if not log_files: return
    latest_log = max(log_files, key=os.path.getmtime)
    
    try:
        with open(latest_log, 'r', encoding='utf-8') as f:
            raw_data = f.read().strip()
        
        if not raw_data: return

        # 실시간 작성 중인 파일 복구 로직 (JSON 형식이 닫히지 않았을 경우)
        # 여러 조합을 시도하여 성공할 때까지 복구 시도
        session_data = None
        for suffix in ['', '}', ']}', ' ] }', ' ] } ] }']:
            try:
                session_data = json.loads(raw_data + suffix)
                break
            except json.JSONDecodeError:
                continue
        
        if session_data is None:
            # 복구 실패 시 스킵하여 데이터 오염 방지
            return

        messages = session_data.get('messages', [])
        if not messages: return

        # 1. MD 파일 저장
        now = datetime.now()
        year_dir = os.path.join(MD_DIR, now.strftime("%Y"))
        month_dir = os.path.join(year_dir, now.strftime("%m"))
        if not os.path.exists(month_dir): os.makedirs(month_dir)

        timestamp = now.strftime("%Y-%m-%d_%H%M%S")
        filepath = os.path.join(month_dir, f"{timestamp}_history.md")
        with open(filepath, 'w', encoding='utf-8') as out:
            out.write(f"# Gemini Session History - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            for entry in messages:
                role = entry.get('type', 'Unknown').capitalize()
                content_raw = entry.get('content', '')
                content = "\n".join([item.get('text', '') for item in content_raw if isinstance(item, dict) and 'text' in item]) if isinstance(content_raw, list) else content_raw
                if content.strip(): out.write(f"### {role}\n\n{content}\n\n---\n\n")

        # 2. 요약본 생성 (DB 및 텍스트 파일 공용)
        summary_content = "[Session Status Update]\n"
        recent_turns = messages[-3:] if len(messages) > 3 else messages
        for turn in recent_turns:
            role = turn.get('type', 'Unknown').capitalize()
            content_raw = turn.get('content', '')
            content = " ".join([item.get('text', '') for item in content_raw if isinstance(item, dict) and 'text' in item]) if isinstance(content_raw, list) else content_raw
            content_summary = content.replace("\n", " ").strip()
            if len(content_summary) > 200: content_summary = content_summary[:197] + "..."
            summary_content += f"- {role}: {content_summary}\n"
        summary_content += "\n[Goal]: 위 맥락을 바탕으로 사용자의 다음 명령을 대기하십시오."

        # 3. 텍스트 파일 업데이트
        with open(CONTEXT_PATH, 'w', encoding='utf-8') as cf:
            cf.write(summary_content)

        # 4. DB 저장
        save_to_db(latest_log, messages, summary_content)
        print(f"[System] 이력 및 컨텍스트 저장 완료.")
    except Exception as e:
        print(f"[System] 저장 중 오류 발생: {e}")

if __name__ == "__main__":
    if "--restore" in sys.argv:
        restore_context_from_db()
    else:
        save_latest_session()
