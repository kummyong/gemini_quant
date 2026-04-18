import sys
import os

# 명시적으로 시스템 패키지 경로 추가
sys.path.append("/usr/lib/python3/dist-packages")
sys.path.append("/usr/local/lib/python3.13/dist-packages")

import subprocess
import time
from datetime import datetime, timedelta, timezone
from stock_trader.telegram_utils import send_telegram_message

# 한국 시간(KST) 설정 (UTC+9)
KST = timezone(timedelta(hours=9))

# 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 가상환경 대신 현재 실행 중인 파이썬 인터프리터 사용
VENV_PYTHON = sys.executable
LOG_DIR = os.path.join(BASE_DIR, "stock_trader/logs")

# 관리할 프로세스 목록 (경로, 설명)
PROCESSES = [
    {"path": "stock_trader/auto_trader.py", "name": "주식 자동매매", "cwd": BASE_DIR},
    {"path": "stock_trader/telegram_listener.py", "name": "텔레그램 리스너", "cwd": BASE_DIR},
    {"path": "stock_trader/system_monitor_loop.py", "name": "시스템 모니터링", "cwd": BASE_DIR},
    {"path": "secretary/auto_sync_history.py", "name": "대화기록 동기화", "cwd": BASE_DIR},
]

def log(msg):
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
    full_msg = f"[{now}] {msg}"
    print(full_msg)
    with open(os.path.join(LOG_DIR, "unified_watchdog.log"), "a") as f:
        f.write(full_msg + "\n")

def run_watchdog():
    log(f"🚀 통합 워치독 감시 시작... (Python: {VENV_PYTHON})")
    send_telegram_message("🛡️ [System] 통합 워치독이 가동되었습니다. 4개 프로세스 감시를 시작합니다.")
    
    # 프로세스 객체 보관용
    running_procs = {}

    # 현재 환경 변수 복사 및 PYTHONPATH 설정
    env = os.environ.copy()
    system_paths = [
        "/usr/local/lib/python3.13/dist-packages",
        "/usr/lib/python3/dist-packages",
        BASE_DIR,
        os.path.join(BASE_DIR, "stock_trader")
    ]
    env["PYTHONPATH"] = ":".join(system_paths)

    while True:
        for p_info in PROCESSES:
            p_path = p_info["path"]
            p_name = p_info["name"]
            
            # 프로세스가 없거나 종료된 경우
            if p_path not in running_procs or running_procs[p_path].poll() is not None:
                if p_path in running_procs:
                    exit_code = running_procs[p_path].poll()
                    log(f"⚠️  [{p_name}] 종료됨 (Exit Code: {exit_code}). 재실행 중...")
                    # send_telegram_message(f"⚠️  [Alert] {p_name} 프로세스가 종료되어 재실행합니다. (Code: {exit_code})")
                
                # 현재 환경 변수와 함께 프로세스 실행
                full_path = os.path.join(BASE_DIR, p_path)
                try:
                    proc = subprocess.Popen(
                        [VENV_PYTHON, full_path],
                        cwd=p_info["cwd"],
                        env=env,
                        stdout=open(os.path.join(LOG_DIR, f"{p_name.replace(' ', '_')}_stdout.log"), "a"),
                        stderr=open(os.path.join(LOG_DIR, f"{p_name.replace(' ', '_')}_stderr.log"), "a")
                    )
                    running_procs[p_path] = proc
                    log(f"✅ [{p_name}] 시작됨 (PID: {proc.pid})")
                except Exception as e:
                    log(f"❌ [{p_name}] 시작 실패: {e}")
        
        time.sleep(10) # 10초마다 체크

if __name__ == "__main__":
    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)
    
    try:
        run_watchdog()
    except KeyboardInterrupt:
        log("🛑 워치독 수동 종료")
