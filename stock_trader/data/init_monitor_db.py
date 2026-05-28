import sys
import os

from stock_trader.config import DB_PATH
from stock_trader.data.db_repository import DbRepository

def init_db():
    print(f"🔄 데이터베이스 및 스키마 초기화 시작...")
    repo = DbRepository(DB_PATH)
    print(f"✅ 데이터베이스 최적화 초기화 완료")
    print(f"   - 모드: WAL (Write-Ahead Logging) 활성화")
    print(f"   - 경로: {DB_PATH}")

if __name__ == "__main__":
    init_db()
