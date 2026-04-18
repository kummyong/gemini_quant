import sqlite3
import os

DB_PATH = "/root/gemini_history/history.db"

def init_db():
    # 폴더가 없으면 생성
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # sessions 테이블: 각 대화 세션의 요약 정보
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_file TEXT UNIQUE,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        summary TEXT
    )
    """)
    
    # messages 테이블: 세션 내 개별 대화 내용
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id INTEGER,
        role TEXT,
        content TEXT,
        order_index INTEGER,
        FOREIGN KEY (session_id) REFERENCES sessions (id)
    )
    """)
    
    conn.commit()
    conn.close()
    print(f"[System] SQLite 데이터베이스가 초기화되었습니다: {DB_PATH}")

if __name__ == "__main__":
    init_db()
