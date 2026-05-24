"""
DART 실시간 공시 감시 모듈
─────────────────────────
보유 종목에 대한 중대 공시(유상증자, 감자, 합병, 부도 등)를 감지하고 텔레그램으로 알림을 보낸다.
unified_watchdog.py에서 장중 30분 간격으로 호출된다.
"""
import os
import sys
import sqlite3
import logging
import datetime

# 경로 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import DB_PATH
from telegram_utils import send_telegram_message
from dart_api import DartAPI

logger = logging.getLogger("DartDisclosureMonitor")

# ── 감시 대상 공시 키워드 및 심각도 ──
ALERT_KEYWORDS = {
    # 🔴 즉시 매도 검토 (심각도: CRITICAL)
    "유상증자": {"severity": "CRITICAL", "emoji": "🔴", "description": "주가 희석 위험"},
    "감자": {"severity": "CRITICAL", "emoji": "🔴", "description": "재무 악화 신호"},
    "주식병합": {"severity": "CRITICAL", "emoji": "🔴", "description": "재무 악화 신호"},
    "영업정지": {"severity": "CRITICAL", "emoji": "🔴", "description": "사업 중단 위험"},
    "부도": {"severity": "CRITICAL", "emoji": "🔴", "description": "기업 존속 위험"},
    "회생절차": {"severity": "CRITICAL", "emoji": "🔴", "description": "기업 존속 위험"},
    "상장폐지": {"severity": "CRITICAL", "emoji": "🔴", "description": "투자금 손실 위험"},
    "횡령": {"severity": "CRITICAL", "emoji": "🔴", "description": "경영 리스크"},
    "배임": {"severity": "CRITICAL", "emoji": "🔴", "description": "경영 리스크"},
    
    # ⚠️ 주의 관찰 (심각도: WARNING)
    "합병": {"severity": "WARNING", "emoji": "⚠️", "description": "불확실성 증가"},
    "분할": {"severity": "WARNING", "emoji": "⚠️", "description": "불확실성 증가"},
    "최대주주변경": {"severity": "WARNING", "emoji": "⚠️", "description": "경영권 변동"},
    "대표이사변경": {"severity": "WARNING", "emoji": "⚠️", "description": "경영진 변동"},
    "자기주식취득": {"severity": "WARNING", "emoji": "⚠️", "description": "주주환원 (긍정적)"},
    
    # 🟢 정보 알림 (심각도: INFO)
    "무상증자": {"severity": "INFO", "emoji": "🟢", "description": "주주환원 신호"},
    "자기주식소각": {"severity": "INFO", "emoji": "🟢", "description": "주주환원 (긍정적)"},
}


def get_holding_tickers() -> list:
    """현재 보유 종목 리스트를 DB에서 조회한다."""
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT stk_cd, stk_nm FROM portfolio_status WHERE rmnd_qty > 0")
            rows = cursor.fetchall()
            return [{"ticker": row["stk_cd"].replace("A", ""), "name": row["stk_nm"]} for row in rows]
    except Exception as e:
        logger.error(f"보유 종목 DB 조회 실패: {e}")
        return []


def run_disclosure_check():
    """보유 종목 대상 DART 공시 감시를 실행한다."""
    logger.info("📡 DART 공시 감시 시작...")
    
    holdings = get_holding_tickers()
    if not holdings:
        logger.info("보유 종목이 없어 공시 감시를 건너뜁니다.")
        return
    
    try:
        dart = DartAPI()
    except Exception as e:
        logger.error(f"DART API 초기화 실패: {e}")
        return
    
    today = datetime.datetime.now().strftime("%Y%m%d")
    alerts_found = []
    
    for stock in holdings:
        ticker = stock["ticker"]
        name = stock["name"]
        corp_code = dart.get_corp_code(ticker)
        
        if not corp_code:
            continue
        
        try:
            disclosures = dart.search_disclosures(
                bgn_de=today,
                end_de=today,
                corp_code=corp_code,
                page_count=10
            )
            
            for disc in disclosures:
                report_name = disc.get("report_nm", "")
                
                # 키워드 매칭
                for keyword, info in ALERT_KEYWORDS.items():
                    if keyword in report_name:
                        alerts_found.append({
                            "name": name,
                            "ticker": ticker,
                            "report_name": report_name,
                            "rcept_dt": disc.get("rcept_dt", ""),
                            "keyword": keyword,
                            **info
                        })
                        break  # 한 공시에 대해 첫 번째 매칭 키워드만 적용
            
            # DART API 호출 간 대기
            import time
            time.sleep(0.3)
            
        except Exception as e:
            logger.warning(f"[{name}] 공시 조회 오류: {e}")
    
    # ── 알림 발송 ──
    if alerts_found:
        # 심각도 순으로 정렬 (CRITICAL > WARNING > INFO)
        severity_order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
        alerts_found.sort(key=lambda x: severity_order.get(x["severity"], 3))
        
        lines = [
            "📢 *[DART 공시 알림]*",
            f"⏰ {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"🔍 보유 {len(holdings)}종목 중 {len(alerts_found)}건 감지",
            "━━━━━━━━━━━━━━"
        ]
        
        for alert in alerts_found:
            lines.append(
                f"{alert['emoji']} *{alert['name']}* ({alert['ticker']})"
            )
            lines.append(f"   📄 {alert['report_name']}")
            lines.append(f"   💡 {alert['description']}")
            lines.append("")
        
        # CRITICAL 공시가 있으면 긴급 문구 추가
        critical_alerts = [a for a in alerts_found if a["severity"] == "CRITICAL"]
        if critical_alerts:
            lines.append("🚨 *CRITICAL 공시가 감지되었습니다!*")
            lines.append("즉시 매도 검토가 필요합니다.")
        
        lines.append("━━━━━━━━━━━━━━")
        
        msg = "\n".join(lines)
        try:
            send_telegram_message(msg)
            logger.info(f"DART 공시 알림 {len(alerts_found)}건 텔레그램 발송 완료")
        except Exception as tg_e:
            logger.error(f"텔레그램 발송 실패: {tg_e}")
    else:
        logger.info(f"✅ 보유 {len(holdings)}종목 대상 금일 특이 공시 없음")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    run_disclosure_check()
