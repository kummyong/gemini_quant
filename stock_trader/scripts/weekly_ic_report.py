"""
주간 팩터 IC 리포트 스크립트
──────────────────────────
factor_analysis의 실거래 IC 분석 결과를 텔레그램으로 전송한다.
"플래그 off로 증거를 쌓다가 검증되면 켠다"는 신호 운영 원칙이 실제로 돌아가려면
누군가 주기적으로 증거를 봐야 하는데, 이 스크립트가 그 역할을 자동화한다.

unified_watchdog가 매주 토요일 09:00에 실행하며, 단독 실행도 가능하다:
    python stock_trader/scripts/weekly_ic_report.py
"""
import io
import os
import sys
import logging
import contextlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from stock_trader.core.factor_analysis import load_outcomes_df, print_report
from stock_trader.communication.telegram_utils import send_telegram_message

logger = logging.getLogger("WeeklyIcReport")

TELEGRAM_MAX_LEN = 3800  # 텔레그램 메시지 한도(4096) 내 안전 마진


def build_report_text() -> str:
    """factor_analysis의 콘솔 리포트를 문자열로 캡처한다."""
    df = load_outcomes_df()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        print_report(df, min_trades=30)
    return buf.getvalue().strip()


def main() -> int:
    try:
        report = build_report_text()
    except Exception as e:
        logger.error(f"❌ IC 리포트 생성 실패: {e}")
        try:
            send_telegram_message(f"⚠️ [주간 IC 리포트] 생성 실패: {e}")
        except Exception:
            pass
        return 1

    if len(report) > TELEGRAM_MAX_LEN:
        report = report[:TELEGRAM_MAX_LEN] + "\n...(길이 제한으로 잘림)"

    msg = f"📈 *[주간 팩터 IC 리포트]*\n```\n{report}\n```"
    try:
        send_telegram_message(msg)
        logger.info("✅ 주간 IC 리포트 전송 완료")
        return 0
    except Exception as e:
        logger.error(f"❌ 텔레그램 전송 실패: {e}")
        print(report)  # 전송 실패 시 stdout으로라도 남긴다 (워치독 로그에 수집됨)
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    sys.exit(main())
