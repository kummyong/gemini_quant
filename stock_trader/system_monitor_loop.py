import time
import subprocess
import os
import fcntl
import sys
from datetime import datetime
import pytz

# 타임존 설정
KST = pytz.timezone('Asia/Seoul')

# 경로 설정
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
LOG_DIR = os.path.join(BASE_DIR, 'logs')
if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)

# 중복 실행 방지 (Lock File)
LOCK_FILE = os.path.join(LOG_DIR, "system_monitor.lock")
fp = open(LOCK_FILE, 'w')
try:
    fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    print("⚠️  System Monitor Loop is already running. Exiting...")
    sys.exit(0)

MONITOR_SCRIPT = os.path.join(BASE_DIR, "system_monitor.py")
TREND_SCRIPT = os.path.join(BASE_DIR, "system_trend_reporter.py")

def run_monitor():
    """매분 시스템 상태 수집 (Silent)"""
    try:
        subprocess.run(["python3", MONITOR_SCRIPT, "--silent"], check=True)
    except Exception as e:
        print(f"❌ 모니터링 수집 에러: {e}")

def run_trend_report(range_type="1h"):
    """트렌드 리포트 생성 및 전송"""
    now_str = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{now_str}] 📈 {range_type} 트렌드 보고서 생성 중...")
    try:
        subprocess.run(["python3", TREND_SCRIPT, range_type], check=True)
    except Exception as e:
        print(f"❌ {range_type} 트렌드 보고 에러: {e}")

if __name__ == "__main__":
    print(f"🚀 [System Monitor Loop v5] 시작됨 (KST 기준: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')})")
    print(" - 매분 정각: 수집")
    print(" - 매시 정각: 1시간 트렌드 보고")
    print(" - 오전 08시: 24시간/1주일/1개월 트렌드 종합 보고")
    
    last_minute = -1
    
    while True:
        now = datetime.now(KST)
        
        # 분이 바뀌었을 때 실행 (정시 동기화)
        if now.minute != last_minute:
            # 1. 시스템 메트릭 수집
            run_monitor()
            
            # 2. 매시 정각: 1시간 트렌드 보고
            if now.minute == 0:
                run_trend_report("1h")
                
                # [추가] 새벽 3시: 전체 모델 정밀 재학습 (Daily Maintenance)
                if now.hour == 3:
                    print(f"[{datetime.now(KST)}] 🧠 정기 지능 유지보수(Full Retraining) 시작...")
                    try:
                        from trainer import retrain_model
                        retrain_model()
                        print("✅ 정기 재학습 완료. 최신 지능이 반영되었습니다.")
                    except Exception as e:
                        print(f"❌ 정기 재학습 에러: {e}")
                
                # 3. 오전 08시: 장기 트렌드 보고 (KST 기준 오전 8시)
                if now.hour == 8:
                    print("🌅 오전 8시(KST) 장기 트렌드 브리핑 시작...")
                    run_trend_report("24h")
                    time.sleep(2)
                    run_trend_report("7d")
                    time.sleep(2)
                    run_trend_report("30d")
            
            last_minute = now.minute
        
        # 다음 정각(00초)까지 대기 시간을 계산하여 정밀도 향상
        now = datetime.now(KST)
        sleep_time = 60 - now.second - (now.microsecond / 1_000_000.0)
        if sleep_time < 0.1: # 너무 짧으면 다음 분으로
            sleep_time += 60
            
        time.sleep(max(0.1, sleep_time))
