import os
import sqlite3
import json
import subprocess
import psutil
from datetime import datetime, timedelta, timezone
from telegram_utils import send_telegram_message

# 한국 시간(KST) 설정 (UTC+9)
KST = timezone(timedelta(hours=9))

DB_PATH = "/root/workspace/gemini-quant/stock_trader/logs/system_monitor.db"

def get_system_metrics():
    # 1. CPU Percent (인터벌을 늘려 정확도 향상)
    cpu_usage = psutil.cpu_percent(interval=1.0)
    
    # 2. Memory Info
    mem = psutil.virtual_memory()
    mem_usage_pct = mem.percent
    
    # 3. Battery Info (시스템 권한에 따라 확인 불가할 수 있음)
    battery_level = "확인불가"
    try:
        # 타임아웃을 짧게 주어 루프가 멈추는 것 방지
        res = subprocess.run(["termux-battery-status"], capture_output=True, text=True, timeout=1)
        if res.returncode == 0:
            batt_data = json.loads(res.stdout)
            battery_level = f"{batt_data.get('percentage', '확인불가')}%"
    except:
        pass

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
        "mem_total_kb": int(mem.total / 1024),
        "mem_used_kb": int(mem.used / 1024),
        "mem_available_kb": int(mem.available / 1024),
        "mem_usage_pct": mem_usage_pct
    }

def save_to_db(metrics):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        # 테이블 컬럼 추가 여부 확인 후 저장
        cursor.execute("PRAGMA table_info(system_metrics)")
        columns = [column[1] for column in cursor.fetchall()]
        
        # 필요한 컬럼이 없으면 추가 (배터리, 온도 등)
        if "cpu_usage" not in columns:
            cursor.execute("ALTER TABLE system_metrics ADD COLUMN cpu_usage REAL")
        if "battery_level" not in columns:
            cursor.execute("ALTER TABLE system_metrics ADD COLUMN battery_level TEXT")
        if "cpu_temp" not in columns:
            cursor.execute("ALTER TABLE system_metrics ADD COLUMN cpu_temp TEXT")
            
        cursor.execute("""
            INSERT INTO system_metrics (
                timestamp, cpu_load_1m, cpu_usage, battery_level, cpu_temp,
                mem_total_kb, mem_used_kb, mem_available_kb, mem_usage_pct
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            metrics["timestamp"], metrics["cpu_load_1m"], metrics["cpu_usage"], 
            str(metrics["battery_level"]), str(metrics["cpu_temp"]),
            metrics["mem_total_kb"], metrics["mem_used_kb"], metrics["mem_available_kb"], metrics["mem_usage_pct"]
        ))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ [DB] 저장 오류: {e}")
        return False

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
    import sys
    silent = "--silent" in sys.argv
    metrics = get_system_metrics()
    if save_to_db(metrics):
        if not silent:
            message = format_metrics_message(metrics)
            send_telegram_message(message)
            print("✅ 메트릭 수집 및 전송 완료.")
        else:
            print(f"📊 메트릭 수집 및 DB 저장 완료 ({metrics['timestamp']})")
    else:
        print("❌ 메트릭 수집 또는 DB 저장 실패.")
