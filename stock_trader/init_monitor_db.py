import sys
import os

# stock_trader 경로를 추가하여 db_repository 임포트 가능하도록 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

try:
    from config import DB_PATH
except ImportError:
    DB_PATH = os.path.join(BASE_DIR, "logs", "system_monitor.db")

from db_repository import DbRepository

def init_db():
    print(f"🔄 데이터베이스 및 스키마 초기화 시작...")
    repo = DbRepository(DB_PATH)
    print(f"✅ 데이터베이스 최적화 초기화 완료")
    print(f"   - 모드: WAL (Write-Ahead Logging) 활성화")
    print(f"   - 경로: {DB_PATH}")

if __name__ == "__main__":
    init_db()
