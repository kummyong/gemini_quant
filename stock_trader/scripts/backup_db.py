"""
SQLite DB 일일 백업 스크립트
──────────────────────────
거래 이력·학습 데이터·포지션 상태가 전부 단일 SQLite 파일(logs/system_monitor.db)에
있으므로, sqlite3의 online backup API로 일관된 스냅샷을 남기고 오래된 백업을 정리한다.
(WAL 모드에서도 backup API는 트랜잭션 일관성이 보장됨 — 파일 복사와 다름)

unified_watchdog가 매 거래일 16:20에 실행하며, 단독 실행도 가능하다:
    python stock_trader/scripts/backup_db.py
"""
import os
import sys
import glob
import sqlite3
import logging
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from stock_trader.config import DB_PATH, STOCK_TRADER_DIR

logger = logging.getLogger("BackupDb")

BACKUP_DIR = os.path.join(STOCK_TRADER_DIR, "backups")
KEEP_COUNT = 7  # 최근 N개 백업 보관


def backup_database(db_path: str = DB_PATH, backup_dir: str = BACKUP_DIR, keep_count: int = KEEP_COUNT) -> str:
    """DB를 backup_dir에 날짜 스냅샷으로 백업하고 오래된 백업을 정리한다.
    반환: 생성된 백업 파일 경로 (실패 시 빈 문자열)."""
    if not os.path.exists(db_path):
        logger.error(f"❌ 백업 대상 DB가 없습니다: {db_path}")
        return ""

    os.makedirs(backup_dir, exist_ok=True)
    date_str = datetime.date.today().strftime("%Y%m%d")
    backup_path = os.path.join(backup_dir, f"system_monitor_{date_str}.db")

    try:
        src = sqlite3.connect(db_path, timeout=30.0)
        try:
            dst = sqlite3.connect(backup_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        size_mb = os.path.getsize(backup_path) / (1024 * 1024)
        logger.info(f"✅ DB 백업 완료: {backup_path} ({size_mb:.1f}MB)")
    except Exception as e:
        logger.error(f"❌ DB 백업 실패: {e}")
        # 실패한 불완전 파일은 남기지 않는다 (복원 시 혼동 방지)
        try:
            if os.path.exists(backup_path):
                os.remove(backup_path)
        except OSError:
            pass
        return ""

    # 오래된 백업 정리 (파일명 날짜 오름차순 정렬 후 앞에서부터 삭제)
    try:
        backups = sorted(glob.glob(os.path.join(backup_dir, "system_monitor_*.db")))
        for old in backups[:-keep_count]:
            os.remove(old)
            logger.info(f"🗑️ 오래된 백업 삭제: {os.path.basename(old)}")
    except Exception as e:
        logger.warning(f"⚠️ 오래된 백업 정리 실패 (백업 자체는 성공): {e}")

    return backup_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    result = backup_database()
    sys.exit(0 if result else 1)
