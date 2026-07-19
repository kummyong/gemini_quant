"""
DART 기반 내부자/대주주 장내매수 이력 수집 모듈
─────────────────────────────────────────────
공시 목록에서 '소유상황보고서'를 필터링한 뒤, 종목별 elestock(임원·주요주주 소유보고)의
같은 rcept_no 행에서 주식 증가(sp_stock_lmp_irds_cnt > 0)를 확인하고, 보고서 원문에
'장내매수'가 명시된 경우에만 저장한다.

elestock.json 응답에는 취득방법 필드가 없어서(실측 필드: repror, sp_stock_lmp_irds_cnt,
rcept_no, rcept_dt 등) 원문 교차 검증 없이는 스톡옵션 행사/상여 지급으로 늘어난 물량을
장내매수로 오인하게 된다.
"""
import logging
import datetime
import time

from stock_trader.data.dart_api import DartAPI
from stock_trader.data.db_repository import DbRepository
from stock_trader.config import DB_PATH

logger = logging.getLogger("SmartMoneyCollector")


def _to_int(val) -> int:
    """DART 수량 필드는 '1,000'처럼 콤마가 섞인 문자열이다."""
    try:
        return int(str(val).replace(",", "").strip() or 0)
    except (TypeError, ValueError):
        return 0


def _normalize_date(rcept_dt: str) -> str:
    """elestock은 '2024-08-01', 공시목록은 '20240801' 형식 — 둘 다 YYYY-MM-DD로 통일."""
    s = (rcept_dt or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return s


# 자산운용사 등 기관의 기계적 매수(ETF 유동성공급/설정, 펀드 리밸런싱)는 임원/대주주의
# 확신 매수와 성격이 달라 스마트 머니 신호에서 제외한다. 실측에서 미래에셋자산운용의
# ETF 장내매수가 대량으로 잡히는 것을 확인함.
_INSTITUTIONAL_REPORTER_KEYWORDS = ("자산운용", "투자자문", "유동성공급")


def _is_institutional_reporter(reporter: str) -> bool:
    name = (reporter or "").replace(" ", "")
    return any(kw in name for kw in _INSTITUTIONAL_REPORTER_KEYWORDS)


def collect_insider_buying(days_back: int = 3):
    """
    최근 N일간의 공시 목록을 조회하여, 임원·주요주주의 '장내매수' 내역을 DB에 캐싱합니다.
    """
    logger.info(f"🔍 DART 스마트 머니(장내매수) 수집 시작 (최근 {days_back}일)")

    try:
        dart = DartAPI()
        db = DbRepository(DB_PATH)
    except Exception as e:
        logger.error(f"❌ 초기화 실패: {e}")
        return

    now = datetime.datetime.now()
    bgn_de = (now - datetime.timedelta(days=days_back)).strftime("%Y%m%d")
    end_de = now.strftime("%Y%m%d")

    try:
        disclosures = []
        for cls in ["Y", "K"]:  # Y=코스피, K=코스닥
            disclosures.extend(dart.search_disclosures_all(bgn_de=bgn_de, end_de=end_de, corp_cls=cls))
            time.sleep(0.3)
    except Exception as e:
        logger.error(f"❌ DART 공시 목록 조회 실패: {e}")
        return

    # 예: 임원ㆍ주요주주특정증권등소유상황보고서
    target_reports = [d for d in disclosures if "소유상황보고" in d.get("report_nm", "")]
    logger.info(f"📄 전체 조회된 공시 {len(disclosures)}건 중 소유상황보고서 {len(target_reports)}건 발견")

    # 종목당 elestock을 1회만 호출하기 위한 캐시: {stock_code: {rcept_no: [rows...]}}
    elestock_cache = {}
    collected_count = 0

    for rep in target_reports:
        stock_code = rep.get("stock_code")
        corp_name = rep.get("corp_name", "")
        report_nm = rep.get("report_nm", "")
        rcept_no = rep.get("rcept_no", "")

        if not stock_code or not rcept_no:
            continue

        try:
            if stock_code not in elestock_cache:
                by_rcept = {}
                for row in dart.get_executive_shareholders(stock_code):
                    by_rcept.setdefault(row.get("rcept_no", ""), []).append(row)
                elestock_cache[stock_code] = by_rcept
                time.sleep(0.3)

            # 1차 필터: 이 보고서(rcept_no)에서 실제로 주식이 증가한 행만
            # (자산운용사 등 기관 보고자의 기계적 매수는 제외)
            increased_rows = [
                r for r in elestock_cache[stock_code].get(rcept_no, [])
                if _to_int(r.get("sp_stock_lmp_irds_cnt")) > 0
                and not _is_institutional_reporter(r.get("repror", ""))
            ]
            if not increased_rows:
                continue

            # 2차 필터: 보고서 원문에 '장내매수' 명시 확인 (스톡옵션/상여 취득 제외)
            doc_text = dart.get_document_text(rcept_no)
            time.sleep(0.3)
            if "장내매수" not in doc_text:
                continue

            for row in increased_rows:
                amount = _to_int(row.get("sp_stock_lmp_irds_cnt"))
                reporter = row.get("repror", "알수없음")
                db.save_insider_buying(
                    ticker=stock_code,
                    date=_normalize_date(row.get("rcept_dt")),
                    title=report_nm,
                    reporter=reporter,
                    reason="장내매수(원문확인)",
                    amount=amount
                )
                logger.info(f"💎 [{corp_name}] 내부자 장내매수 포착: {reporter} (+{amount:,}주)")
                collected_count += 1
        except Exception as e:
            logger.warning(f"⚠️ [{corp_name}] 소유상황 세부정보 파싱 실패: {e}")

    logger.info(f"✅ 스마트 머니 수집 완료 (총 {collected_count}건 장내매수 DB 저장)")


if __name__ == "__main__":
    # 단독 실행 시 테스트 용도
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    collect_insider_buying(3)
