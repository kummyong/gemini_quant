#!/bin/bash

# 무한 루프를 돌며 unified_watchdog.py가 종료되면 5초 뒤 재시작합니다.
while true; do
    echo "[$(date)] Starting unified_watchdog.py..."
    python3 unified_watchdog.py
    EXIT_CODE=$?
    echo "[$(date)] unified_watchdog.py exited with code $EXIT_CODE. Restarting in 5 seconds..."
    sleep 5
done
