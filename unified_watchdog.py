import sys
import os

# 명시적으로 시스템 패키지 경로 추가
sys.path.append("/usr/lib/python3/dist-packages")
sys.path.append("/usr/local/lib/python3.13/dist-packages")

import subprocess
import time
from datetime import datetime, timedelta, timezone
from stock_trader.communication.telegram_utils import send_telegram_message

# 한국 시간(KST) 설정 (UTC+9)
KST = timezone(timedelta(hours=9))

# 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# 가상환경 대신 현재 실행 중인 파이썬 인터프리터 사용
VENV_PYTHON = sys.executable
LOG_DIR = os.path.join(BASE_DIR, "stock_trader/logs")

# 관리할 프로세스 목록 (경로, 설명)
PROCESSES = [
    {"path": "stock_trader/core/auto_trader.py", "name": "주식 자동매매", "cwd": BASE_DIR},
    {"path": "stock_trader/communication/telegram_listener.py", "name": "텔레그램 리스너", "cwd": BASE_DIR},
    {"path": "stock_trader/monitoring/system_monitor_loop.py", "name": "시스템 모니터링", "cwd": BASE_DIR},
    {"path": "secretary/auto_sync_history.py", "name": "대화기록 동기화", "cwd": BASE_DIR},
    {"path": "stock_trader/communication/notification_gateway.py", "name": "알림 게이트웨이", "cwd": BASE_DIR},
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
        
        from stock_trader.core.korean_market_calendar import is_market_holiday
        is_today_holiday = is_market_holiday(now)
        
        # [스케줄링] 전략 엔진 실행 (장 운영일 기준 프로파일별 시간 조정)
        from stock_trader.config import ACTIVE_STRATEGY_PROFILE, ETF_TREND
        if ACTIVE_STRATEGY_PROFILE == ETF_TREND:
            target_hour, target_minute = 15, 45  # 장 마감 후 1일 1회 실행
        else:
            target_hour, target_minute = 8, 30   # 개장 전 실행
            
        # 정각(분) 일치 대신 30분 윈도우: 직전 작업이 오래 걸려 해당 분을 놓쳐도 실행됨
        strategy_target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        if not is_today_holiday and strategy_target <= now < strategy_target + timedelta(minutes=30):
            today_str = now.strftime("%Y-%m-%d")
            if last_strategy_run != today_str:
                log(f"📅 [Schedule] 전략 엔진 자동 실행 시간 ({target_hour:02d}:{target_minute:02d})")
                strategy_path = os.path.join(BASE_DIR, "stock_trader/core/strategy_engine.py")
                try:
                    subprocess.run([VENV_PYTHON, strategy_path], env=env, check=True, timeout=1800)
                    log("✨ [Schedule] 전략 엔진 실행 완료")
                    last_strategy_run = today_str
                except Exception as e:
                    log(f"❌ [Schedule] 전략 엔진 실행 실패: {e}")

        # [스케줄링] 일일 마감 보고 (장 운영일 15:40 이후 30분 윈도우 내 한 번만)
        summary_target = now.replace(hour=15, minute=40, second=0, microsecond=0)
        if not is_today_holiday and summary_target <= now < summary_target + timedelta(minutes=30):
            today_str = now.strftime("%Y-%m-%d")
            if last_summary_run != today_str:
                log("📅 [Schedule] 일일 마감 보고 전송")
                try:
                    subprocess.run(
                        [VENV_PYTHON, "-c",
                         f"import sys; sys.path.insert(0, '{BASE_DIR}'); "
                         f"from stock_trader.core.summary_trader import send_daily_summary_to_telegram; "
                         f"send_daily_summary_to_telegram()"],
                        env=env, check=True, timeout=120  # 실계좌 조회 + 포지션 대사 시간 포함
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
                dart_monitor_path = os.path.join(BASE_DIR, "stock_trader/data/dart_disclosure_monitor.py")
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
                expander_path = os.path.join(BASE_DIR, "stock_trader/data/dart_universe_expander.py")
                try:
                    subprocess.run([VENV_PYTHON, expander_path], env=env, check=True, timeout=600)
                    last_universe_expand = current_month
                    log("✅ [Schedule] 유니버스 확장 완료")
                except Exception as e:
                    log(f"❌ [Schedule] 유니버스 확장 실패: {e}")

        for p_info in PROCESSES:
            p_path = p_info["path"]
            p_name = p_info["name"]
            
            entry = running_procs.get(p_path)
            # 프로세스가 없거나 종료된 경우
            if entry is None or entry["proc"].poll() is not None:
                if entry is not None:
                    exit_code = entry["proc"].poll()
                    log(f"⚠️  [{p_name}] 종료됨 (Exit Code: {exit_code}). 재실행 중...")
                    # 재시작마다 로그 파일 핸들이 새로 열리므로 이전 핸들을 닫아 fd 누수 방지
                    for f in entry["files"]:
                        try:
                            f.close()
                        except Exception:
                            pass

                # 현재 환경 변수와 함께 프로세스 실행
                full_path = os.path.join(BASE_DIR, p_path)
                try:
                    f_out = open(os.path.join(LOG_DIR, f"{p_name.replace(' ', '_')}_stdout.log"), "a")
                    f_err = open(os.path.join(LOG_DIR, f"{p_name.replace(' ', '_')}_stderr.log"), "a")
                    proc = subprocess.Popen(
                        [VENV_PYTHON, full_path],
                        cwd=p_info["cwd"],
                        env=env,
                        stdout=f_out,
                        stderr=f_err
                    )
                    running_procs[p_path] = {"proc": proc, "files": (f_out, f_err)}
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
