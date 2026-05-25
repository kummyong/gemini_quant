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
    last_strategy_run = None
    last_summary_run = None
    last_dart_check_minute = -1  # DART 공시 감시 마지막 실행 시각 (분 단위)
    last_universe_expand = None  # 유니버스 확장 마지막 실행 월

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
        now = datetime.now(KST)
        
        from stock_trader.korean_market_calendar import is_market_holiday
        is_today_holiday = is_market_holiday(now)
        
        # [스케줄링] 전략 엔진 실행 (장 운영일 08:30:00 ~ 08:30:30 사이 한 번만)
        if not is_today_holiday and now.hour == 8 and now.minute == 30:
            today_str = now.strftime("%Y-%m-%d")
            if last_strategy_run != today_str:
                log("📅 [Schedule] 전략 엔진 자동 실행 시간 (08:30)")
                strategy_path = os.path.join(BASE_DIR, "stock_trader/strategy_engine.py")
                try:
                    subprocess.run([VENV_PYTHON, strategy_path], env=env, check=True)
                    log("✨ [Schedule] 전략 엔진 실행 완료")
                    last_strategy_run = today_str
                    # 중복 방지를 위해 기존 제네릭 메시지는 주석 처리합니다. 상세 정보는 strategy_engine.py에서 직접 보냅니다.
                    # send_telegram_message("🤖 [전략] 오늘 자 전략 종목 선정이 완료되었습니다.")
                except Exception as e:
                    log(f"❌ [Schedule] 전략 엔진 실행 실패: {e}")

        # [스케줄링] 일일 마감 보고 (장 운영일 15:40:00 ~ 15:40:30 사이 한 번만)
        if not is_today_holiday and now.hour == 15 and now.minute == 40:
            today_str = now.strftime("%Y-%m-%d")
            if last_summary_run != today_str:
                log("📅 [Schedule] 일일 마감 보고 전송")
                try:
                    subprocess.run(
                        [VENV_PYTHON, "-c",
                         f"import sys; sys.path.insert(0, '{os.path.join(BASE_DIR, 'stock_trader')}'); "
                         f"from summary_trader import send_daily_summary_to_telegram; "
                         f"send_daily_summary_to_telegram()"],
                        env=env, check=True, timeout=30
                    )
                    last_summary_run = today_str
                    log("✨ [Schedule] 일일 마감 보고 전송 완료")
                except Exception as e:
                    log(f"❌ [Schedule] 일일 마감 보고 전송 실패: {e}")

        # 주말 파라미터 자가 학습은 PC(Trainer Node)에서 처리하므로 모바일 스케줄은 제거함

        # [스케줄링] DART 공시 감시 (장중 매 30분 간격: 09:00, 09:30, 10:00, ..., 15:00)
        if not is_today_holiday and 9 <= now.hour < 16:
            current_check_minute = now.hour * 60 + (now.minute // 30) * 30  # 30분 단위로 정규화
            if current_check_minute != last_dart_check_minute:
                log("📡 [Schedule] DART 공시 감시 실행")
                dart_monitor_path = os.path.join(BASE_DIR, "stock_trader/dart_disclosure_monitor.py")
                try:
                    subprocess.run([VENV_PYTHON, dart_monitor_path], env=env, check=True, timeout=120)
                    last_dart_check_minute = current_check_minute
                    log("✅ [Schedule] DART 공시 감시 완료")
                except Exception as e:
                    log(f"❌ [Schedule] DART 공시 감시 실패: {e}")
                    
        # [스케줄링] 유니버스 자동 확장 (매월 1일 08:00)
        if now.day == 1 and now.hour == 8 and now.minute < 1:
            current_month = now.strftime("%Y-%m")
            if last_universe_expand != current_month:
                log("📋 [Schedule] DART 기반 유니버스 확장 실행")
                expander_path = os.path.join(BASE_DIR, "stock_trader/dart_universe_expander.py")
                try:
                    subprocess.run([VENV_PYTHON, expander_path], env=env, check=True, timeout=600)
                    last_universe_expand = current_month
                    log("✅ [Schedule] 유니버스 확장 완료")
                except Exception as e:
                    log(f"❌ [Schedule] 유니버스 확장 실패: {e}")

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
