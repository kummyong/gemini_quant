import time
import os
import sys
import logging
from logging.handlers import RotatingFileHandler
import fcntl
import pytz
from datetime import datetime

# 타임존 설정 (KST)
KST = pytz.timezone('Asia/Seoul')

def kst_converter(*args):
    return datetime.now(KST).timetuple()

# config.py 임포트를 위해 stock_trader 경로 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
stock_trader_dir = os.path.join(os.path.dirname(current_dir), "stock_trader")
if stock_trader_dir not in sys.path:
    sys.path.insert(0, stock_trader_dir)

# 중복 실행 방지 (Lock File)
from config import LOG_DIR, SECRETARY_DIR

LOCK_FILE = os.path.join(LOG_DIR, "auto_sync_history.lock")
fp = open(LOCK_FILE, 'w')
try:
    fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    print("⚠️  Auto Sync History is already running. Exiting...")
    sys.exit(0)

# 기존 save_history 기능을 활용하기 위해 경로 추가
BASE_DIR = SECRETARY_DIR
sys.path.append(BASE_DIR)

try:
    from save_history import save_latest_session
except ImportError:
    logging.error("❌ save_history.py를 찾을 수 없습니다.")
    sys.exit(1)

# 로그 설정 (KST 적용)
LOG_FILE = os.path.join(LOG_DIR, "auto_sync_history.log")
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
formatter.converter = kst_converter

file_handler = RotatingFileHandler(LOG_FILE, maxBytes=2*1024*1024, backupCount=2)
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler(sys.stdout)
stream_handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler]
)

def main():
    logging.info("🚀 히스토리 자동 동기화 데몬 시작 (주기: 10분)")
    
    while True:
        try:
            # 히스토리 저장 실행
            save_latest_session()
            logging.info("✅ 세션 히스토리 동기화 완료")
        except Exception as e:
            logging.error(f"🔥 동기화 중 에러 발생: {e}")
        
        # 10분 대기 (600초)
        time.sleep(600)

if __name__ == "__main__":
    # 백그라운드 실행 시 stdout 버퍼링 해제
    os.environ['PYTHONUNBUFFERED'] = '1'
    main()
