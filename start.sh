#!/bin/bash
BASE_DIR="/root/workspace/gemini-quant"
LOG_DIR="$BASE_DIR/stock_trader/logs"

mkdir -p $LOG_DIR
cd $BASE_DIR

# 이미 실행 중인지 확인
if pgrep -f "unified_watchdog.py" > /dev/null; then
    echo "⚠️  통합 워치독이 이미 실행 중입니다."
    exit 1
fi

echo "🚀 Gemini Quant 서비스를 시작합니다..."
nohup python3 unified_watchdog.py > $LOG_DIR/unified_watchdog.out 2>&1 &

echo "✅ 시작 완료. 로그 확인: tail -f $LOG_DIR/unified_watchdog.log"
