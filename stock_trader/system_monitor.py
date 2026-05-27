import os
import sys
import json
import subprocess
try:
    import psutil
except (ImportError, NotImplementedError):
    psutil = None
from datetime import datetime, timedelta, timezone

# stock_trader 경로 추가
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from config import DB_PATH
from db_repository import DbRepository
from ipc_messenger import IpcPublisher

# 한국 시간(KST) 설정 (UTC+9)
KST = timezone(timedelta(hours=9))

def get_system_metrics():
    # 1. CPU Percent (인터벌을 늘려 정확도 향상)
    cpu_usage = psutil.cpu_percent(interval=1.0) if psutil else 0.0
    
    # 2. Memory Info
    if psutil:
        mem = psutil.virtual_memory()
        mem_usage_pct = mem.percent
        mem_total_kb = int(mem.total / 1024)
        mem_used_kb = int(mem.used / 1024)
        mem_available_kb = int(mem.available / 1024)
    else:
        mem_usage_pct = 0.0
        mem_total_kb = 0
        mem_used_kb = 0
        mem_available_kb = 0
    
    # 3. Battery Info
    battery_level = "N/A"

    # 4. Temperature (CPU 온도 시도)
    cpu_temp = "N/A"
    try:
        # 일반적인 리눅스 온도 경로
        temp_paths = [
            "/sys/class/thermal/thermal_zone0/temp",
            "/sys/class/hwmon/hwmon0/temp1_input"
        ]
        for path in temp_paths:
            if os.path.exists(path):
                with open(path, 'r') as f:
                    cpu_temp = round(int(f.read().strip()) / 1000, 1)
                break
    except:
        pass

    return {
        "timestamp": datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S"),
        "cpu_usage": cpu_usage,
        "memory_usage": mem_usage_pct,
        "battery_level": battery_level,
        "cpu_temp": cpu_temp,
        # 하위 호환성을 위한 기존 키 유지
        "cpu_load_1m": os.getloadavg()[0] if hasattr(os, 'getloadavg') else 0.0,
        "mem_total_kb": mem_total_kb,
        "mem_used_kb": mem_used_kb,
        "mem_available_kb": mem_available_kb,
        "mem_usage_pct": mem_usage_pct
    }

def format_metrics_message(metrics):
    # ResponseFormatter와 유사한 형식으로 직접 보고서 생성 (독립 실행용)
    emoji = "✅" if metrics["cpu_usage"] < 80 else "⚠️"
    msg = f"📱 **[시스템/휴대폰 상태 보고]**\n"
    msg += f"━━━━━━━━━━━━━━\n"
    msg += f"{emoji} **CPU 사용률:** {metrics['cpu_usage']}%\n"
    msg += f"🧠 **메모리 사용률:** {metrics['memory_usage']}%\n"
    msg += f"🔋 **배터리 잔량:** {metrics['battery_level']}%\n"
    msg += f"🌡️ **기기 온도:** {metrics['cpu_temp']}°C\n"
    msg += f"━━━━━━━━━━━━━━\n"
    msg += f"🕒 일시: `{metrics['timestamp']}`"
    return msg

if __name__ == "__main__":
    silent = "--silent" in sys.argv
    metrics = get_system_metrics()
    
    # 1. DbRepository를 사용하여 데이터 저장
    repo = DbRepository(DB_PATH)
    try:
        repo.save_system_metric(
            timestamp=metrics["timestamp"],
            cpu_load_1m=metrics["cpu_load_1m"],
            cpu_usage=metrics["cpu_usage"],
            battery_level=metrics["battery_level"],
            cpu_temp=str(metrics["cpu_temp"]),
            mem_total=metrics["mem_total_kb"],
            mem_used=metrics["mem_used_kb"],
            mem_avail=metrics["mem_available_kb"],
            mem_pct=metrics["mem_usage_pct"]
        )
        save_success = True
    except Exception as e:
        print(f"❌ [DB] 저장 오류: {e}")
        save_success = False

    if save_success:
        if not silent:
            # 2. IpcPublisher를 통해 알림 이벤트 전송 (직접 텔레그램 전송 대체)
            message = format_metrics_message(metrics)
            publisher = IpcPublisher()
            sent = publisher.send_message_sync("SYSTEM_ALERT", {"message": message})
            if sent:
                print("✅ 메트릭 수집 및 IPC 이벤트 발송 완료.")
            else:
                print("⚠️ 메트릭 저장 완료되었으나 IPC 전송 실패.")
        else:
            print(f"📊 메트릭 수집 및 DB 저장 완료 ({metrics['timestamp']})")
    else:
        print("❌ 메트릭 수집 또는 DB 저장 실패.")
