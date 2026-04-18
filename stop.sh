#!/bin/bash
echo "🛑 Gemini Quant 서비스를 종료합니다..."

# 워치독 먼저 종료 (재실행 방지)
pkill -f unified_watchdog.py

# 하위 프로세스들 정리
pkill -f auto_trader.py
pkill -f telegram_listener.py
pkill -f system_monitor_loop.py
pkill -f auto_sync_history.py

echo "✅ 모든 프로세스가 종료되었습니다."
