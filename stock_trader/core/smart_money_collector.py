"""
DART 기반 내부자/대주주 장내매수 이력 수집 모듈
─────────────────────────────────────────────
최근 공시 목록에서 '소유상황보고서'만 필터링하여 장내매수 내역을 수집합니다.
전 종목을 순회하지 않고 공시가 있는 종목만 핀셋 조회하므로 DART API 호출 한도를 절약합니다.
"""
import logging
import datetime
import time

from stock_trader.data.dart_api import DartAPI
from stock_trader.data.db_repository import DbRepository
from stock_trader.config import DB_PATH

logger = logging.getLogger("SmartMoneyCollector")

def collect_insider_buying(days_back: int = 3):
    """
    최근 N일간의 공시 목록을 조회하여, 임원 및 주요주주의 '장내매수' 내역을 DB에 캐싱합니다.
    """
    logger.info(f"🔍 DART 스마트 머니(장내매수) 수집 시작 (최근 {days_back}일)")
    
    try:
        dart = DartAPI()
        db = DbRepository(DB_PATH)
    except Exception as e:
        logger.error(f"❌ 초기화 실패: {e}")
        return
    
    now = datetime.datetime.now()
    start_date = now - datetime.timedelta(days=days_back)
    
    bgn_de = start_date.strftime("%Y%m%d")
    end_de = now.strftime("%Y%m%d")
    
    try:
        # 전체 시장 대상 특정 기간의 공시 리스트업 (코스피, 코스닥)
        disclosures = []
        for cls in ["Y", "K"]: # Y=코스피, K=코스닥
            res = dart.search_disclosures(bgn_de=bgn_de, end_de=end_de, corp_code="", corp_cls=cls, page_count=100)
            if res:
                disclosures.extend(res)
            time.sleep(0.3)
            
    except Exception as e:
        logger.error(f"❌ DART 공시 목록 조회 실패: {e}")
        return
        
    # '소유상황보고'라는 키워드가 들어간 보고서만 필터링
    # 예: 임원ㆍ주요주주특정증권등소유상황보고서
    target_reports = [d for d in disclosures if "소유상황보고" in d.get("report_nm", "")]
    
    logger.info(f"📄 전체 조회된 공시 {len(disclosures)}건 중 소유상황보고서 {len(target_reports)}건 발견")
    
    collected_count = 0
    for rep in target_reports:
        stock_code = rep.get("stock_code")
        corp_name = rep.get("corp_name", "")
        report_nm = rep.get("report_nm", "")
        rcept_dt = rep.get("rcept_dt", end_de) # YYYYMMDD
        
        if not stock_code:
            continue
            
        try:
            # 해당 종목에 대해 상세 API 호출
            exec_data = dart.get_executive_shareholders(stock_code)
            for detail in exec_data:
                reason = detail.get("sttus_vrfc", "") or detail.get("reprt_resn", "")
                reporter = detail.get("reprt_nm", detail.get("nm", "알수없음"))
                amount_str = detail.get("spfc_isks_icdc_num", "0").replace(",", "")
                
                try:
                    amount = int(amount_str)
                except:
                    amount = 0
                
                # '장내매수'라는 단어가 변동사유에 있고, 실제로 주식이 증가(+)한 경우
                if "장내매수" in reason and amount > 0:
                    formatted_date = f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:8]}"
                    db.save_insider_buying(
                        ticker=stock_code,
                        date=formatted_date,
                        title=report_nm,
                        reporter=reporter,
                        reason=reason,
                        amount=amount
                    )
                    logger.info(f"💎 [{corp_name}] 내부자 장내매수 포착: {reporter} (+{amount}주)")
                    collected_count += 1
            
            # API 제한 방지를 위한 딜레이
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"⚠️ [{corp_name}] 소유상황 세부정보 파싱 실패: {e}")
            
    logger.info(f"✅ 스마트 머니 수집 완료 (총 {collected_count}건 장내매수 DB 저장)")

if __name__ == "__main__":
    # 단독 실행 시 테스트 용도
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    collect_insider_buying(3)
