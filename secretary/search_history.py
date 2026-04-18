import sqlite3
import os
import glob
import sys

# 설정: 이력이 저장된 폴더
HISTORY_DIR = "/root/gemini_history"
DB_DIR = os.path.join(HISTORY_DIR, "database")

def search_history(keyword):
    # 모든 history_*.db 파일을 찾음
    db_files = glob.glob(os.path.join(DB_DIR, "history_*.db"))
    db_files.sort(reverse=True)  # 최신 달부터 검색
    
    if not db_files:
        print("[System] 검색할 데이터베이스 파일이 없습니다.")
        return

    found_count = 0
    print(f"\n[Search] 키워드 '{keyword}' 검색 결과:\n" + "="*60)

    for db_path in db_files:
        db_name = os.path.basename(db_path)
        try:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # messages와 sessions 테이블을 JOIN하여 검색
            query = """
            SELECT s.timestamp, s.summary, m.role, m.content 
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.content LIKE ?
            ORDER BY s.timestamp DESC, m.order_index ASC
            """
            cursor.execute(query, (f'%{keyword}%',))
            results = cursor.fetchall()
            
            if results:
                print(f"\n📂 Database: {db_name}")
                for row in results:
                    timestamp, summary, role, content = row
                    found_count += 1
                    
                    # 결과 출력 형식화
                    print("-" * 40)
                    print(f"[{timestamp}] {role}")
                    # 검색 키워드 주변 텍스트만 보여주기 (너무 길 수 있으므로)
                    start_idx = max(0, content.find(keyword) - 50)
                    snippet = content[start_idx:start_idx + 150].replace("\n", " ").strip()
                    print(f"Content: ...{snippet}...")
            
            conn.close()
        except Exception as e:
            print(f"[Error] {db_name} 검색 중 오류: {e}")

    print("\n" + "="*60)
    print(f"[Result] 총 {found_count}개의 매칭 항목을 찾았습니다.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python search_history.py [검색어]")
    else:
        search_history(sys.argv[1])
