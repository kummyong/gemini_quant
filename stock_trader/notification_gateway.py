import asyncio
import logging
import os
import sys

# stock_trader 경로 추가
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from ipc_messenger import IpcListener
from telegram_utils import send_telegram_message

# 로거 구성
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("NotificationGateway")

async def process_notification(message: dict):
    """IPC 수신 메시지 파싱 및 텔레그램 전송 핸들러"""
    event = message.get("event")
    payload = message.get("payload", {})
    logger.info(f"📨 IPC 이벤트 수신: event={event}")

    # 메시지 유형에 따른 템플릿 처리
    if event == "GLOBAL_STOP_LOSS":
        msg = (
            f"🚨🚨🚨 *[긴급] 글로벌 손절 발동*\n"
            f"━━━━━━━━━━━━━━\n"
            f"📉 포트폴리오 전체 수익률: {payload.get('profit_rate', 0.0):.2f}%\n"
            f"⚠️ 손절선: {payload.get('threshold', 0.0):.2f}%\n"
            f"🔴 전 보유종목 전량 매도 시그널 생성\n"
            f"━━━━━━━━━━━━━━"
        )
    elif event == "TRADE_SIGNAL":
        action = payload.get("action", "UNKNOWN")
        emoji = "🟢 [매수 신호]" if action == "BUY" else "🔴 [매도 신호]"
        msg = (
            f"{emoji} *{payload.get('name')}* ({payload.get('ticker')})\n"
            f"━━━━━━━━━━━━━━\n"
            f"📈 수량: {payload.get('quantity', 0)}주\n"
            f"💡 사유: {payload.get('reason', 'N/A')}\n"
            f"━━━━━━━━━━━━━━"
        )
    elif event == "SYSTEM_ALERT":
        msg = (
            f"⚠️ *[시스템 경보]*\n"
            f"━━━━━━━━━━━━━━\n"
            f"📝 내용: {payload.get('message')}\n"
            f"━━━━━━━━━━━━━━"
        )
    else:
        # 일반 텍스트 알림
        msg = payload.get("message", str(message))

    # 비동기로 텔레그램 메시지 발송 실행 (실제 전송은 requests 동기 함수이므로 루프를 방해하지 않게 run_in_executor 활용)
    loop = asyncio.get_running_loop()
    success = await loop.run_in_executor(None, send_telegram_message, msg)
    if success:
        logger.info(f"✅ 텔레그램 알림 발송 완료: event={event}")
    else:
        logger.error(f"❌ 텔레그램 알림 발송 실패: event={event}")

async def main():
    logger.info("🚀 텔레그램 알림 게이트웨이 구동 준비 중...")
    listener = IpcListener(callback=process_notification)
    
    try:
        await listener.start()
    except KeyboardInterrupt:
        logger.info("👋 게이트웨이 수동 종료 중...")
    finally:
        await listener.stop()

if __name__ == "__main__":
    asyncio.run(main())
