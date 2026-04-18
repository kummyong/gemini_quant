import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import os
import sys
from datetime import datetime, timedelta
from telegram_utils import send_telegram_photo

DB_PATH = "/root/workspace/gemini-quant/stock_trader/logs/system_monitor.db"
LOG_DIR = "/root/workspace/gemini-quant/stock_trader/logs"

def generate_trend_report(range_type="1h"):
    if not os.path.exists(DB_PATH):
        return None, None, "데이터베이스 파일이 존재하지 않습니다."

    try:
        now = datetime.now()
        labels = {"1h": "1시간", "24h": "1일(24시간)", "7d": "1주일", "30d": "1개월"}
        label = labels.get(range_type, range_type)

        if range_type == "1h":
            start_time = now - timedelta(hours=1)
            title = "System Performance Trend (Last 1 Hour)"
            output_name = "system_trend_1h.png"
        elif range_type == "24h":
            start_time = now - timedelta(days=1)
            title = "System Performance Trend (Last 24 Hours)"
            output_name = "system_trend_24h.png"
        elif range_type == "7d":
            start_time = now - timedelta(days=7)
            title = "System Performance Trend (Last 7 Days)"
            output_name = "system_trend_7d.png"
        elif range_type == "30d":
            start_time = now - timedelta(days=30)
            title = "System Performance Trend (Last 30 Days)"
            output_name = "system_trend_30d.png"
        else:
            return None, None, f"지원하지 않는 범위 타입입니다: {range_type}"

        output_path = os.path.join(LOG_DIR, output_name)
        start_time_str = start_time.strftime("%Y-%m-%d %H:%M:%S")
        
        conn = sqlite3.connect(DB_PATH)
        query = f"SELECT timestamp, cpu_load_1m, mem_usage_pct FROM system_metrics WHERE timestamp >= '{start_time_str}' ORDER BY timestamp ASC"
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            return None, None, f"{label} 동안 표시할 데이터가 없습니다."

        # 통계 계산
        cpu_avg = df["cpu_load_1m"].mean()
        cpu_max = df["cpu_load_1m"].max()
        mem_avg = df["mem_usage_pct"].mean()
        mem_max = df["mem_usage_pct"].max()

        summary_text = f"📊 *최근 {label} 시스템 성능 요약*\n\n"
        summary_text += f"🖥️ *CPU Load (1m)*\n"
        summary_text += f" - 평균: `{cpu_avg:.2f}`\n"
        summary_text += f" - 최대: `{cpu_max:.2f}`\n\n"
        summary_text += f"🧠 *Memory Usage (%)*\n"
        summary_text += f" - 평균: `{mem_avg:.1f}%`\n"
        summary_text += f" - 최대: `{mem_max:.1f}%` \n\n"
        summary_text += f"🕒 보고서 생성: `{now.strftime('%H:%M:%S')}`"

        df["timestamp"] = pd.to_datetime(df["timestamp"])

        # 그래프 생성
        plt.figure(figsize=(12, 6))
        
        # CPU 부하 (왼쪽 Y축)
        ax1 = plt.gca()
        ax1.plot(df["timestamp"], df["cpu_load_1m"], color='tab:red', linewidth=1.5, label='CPU Load (1m)')
        ax1.set_xlabel('Time')
        ax1.set_ylabel('CPU Load', color='tab:red', fontsize=12)
        ax1.tick_params(axis='y', labelcolor='tab:red')
        
        # 메모리 사용률 (오른쪽 Y축)
        ax2 = ax1.twinx()
        ax2.plot(df["timestamp"], df["mem_usage_pct"], color='tab:blue', linewidth=1.5, label='Mem Usage (%)')
        ax2.set_ylabel('Memory Usage (%)', color='tab:blue', fontsize=12)
        ax2.tick_params(axis='y', labelcolor='tab:blue')
        ax2.set_ylim(0, 100)

        plt.title(title, fontsize=14, pad=20)
        ax1.grid(True, linestyle='--', alpha=0.5)
        
        # X축 시간 포맷팅 최적화
        if range_type in ["7d", "30d"]:
            import matplotlib.dates as mdates
            ax1.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d %H:%M'))
        else:
            plt.gcf().autofmt_xdate()
        
        plt.tight_layout()
        plt.savefig(output_path, dpi=150)
        plt.close()
        
        return output_path, summary_text, None

    except Exception as e:
        return None, None, f"보고서 생성 오류 ({range_type}): {e}"

if __name__ == "__main__":
    # 인자로 범위 타입을 받음 (기본값 1h)
    range_arg = sys.argv[1] if len(sys.argv) > 1 else "1h"
    
    photo, summary, error = generate_trend_report(range_arg)
    if photo:
        send_telegram_photo(photo, summary)
        print(f"✅ [{range_arg}] 트렌드 보고서 및 요약 전송 완료: {photo}")
    else:
        print(f"❌ [{range_arg}] 보고서 생성 실패: {error}")
