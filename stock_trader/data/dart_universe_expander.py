"""
DART 기반 종목 유니버스 자동 확장
────────────────────────────────
매월 1회 실행되어 DART 재무제표 기준으로 투자 유니버스를 자동 갱신한다.
"""
import os
import sys
import json
import logging
import datetime

from stock_trader.data.dart_api import DartAPI
from stock_trader.config import LOG_DIR

logger = logging.getLogger("DartUniverseExpander")


def expand_universe():
    """DART 재무제표 및 FinanceDataReader 기반으로 종목 유니버스를 자동 확장한다."""
    import FinanceDataReader as fdr
    import time
    
    dart = DartAPI()
    if not dart.corp_code_map:
        logger.error("DART 고유번호 매핑이 비어 있습니다.")
        return
        
    # 기존 유니버스 로드 (정적 목록)
    from stock_trader.core.stock_universe import SAMPLE_TICKERS
    existing_tickers = set(t[0] for t in SAMPLE_TICKERS)
    
    try:
        # 전체 KRX 종목 로드
        logger.info("KRX Stock List fetching...")
        df_krx = fdr.StockListing('KRX')
        
        # KOSPI / KOSDAQ 필터링
        df_krx = df_krx[df_krx['Market'].isin(['KOSPI', 'KOSDAQ'])]
        
        # 우선주 필터링 (코드 끝이 0이 아니거나, 이름에 '우'가 들어간 경우)
        df_krx = df_krx[df_krx['Code'].str.endswith('0')]
        df_krx = df_krx[~df_krx['Name'].str.endswith('우')]
        df_krx = df_krx[~df_krx['Name'].str.endswith('우B')]
        df_krx = df_krx[~df_krx['Name'].str.endswith('우C')]
        
        # 시가총액 기준 내림차순 정렬
        if 'Marcap' in df_krx.columns:
            df_krx = df_krx.sort_values(by='Marcap', ascending=False)
        else:
            logger.error("시가총액 컬럼(Marcap)을 찾을 수 없습니다.")
            return
            
        # 상위 300위 선정
        top_300 = df_krx.head(300)
    except Exception as e:
        logger.error(f"KRX 종목 리스트 및 시총 순위 조회 실패: {e}")
        return
        
    candidates = []
    now = datetime.datetime.now()
    check_year = now.year - 1 if now.month > 3 else now.year - 2
    
    # 상위 300개 종목 중 현재 유니버스에 없는 종목들을 스캔
    checked = 0
    for idx, row in top_300.iterrows():
        stock_code = row['Code']
        corp_name = row['Name']
        
        if stock_code in existing_tickers:
            continue
            
        # DART 고유번호 매핑 확인
        if stock_code not in dart.corp_code_map:
            continue
            
        # API 호출 개수 제약 (신규 후보 탐색 한도 설정)
        if checked >= 40:
            break
            
        # 1. 거래대금 필터링 (최근 20일 평균 일일 거래대금 >= 10억 원)
        try:
            start_date = (now - datetime.timedelta(days=40)).strftime('%Y-%m-%d')
            df_hist = fdr.DataReader(stock_code, start=start_date)
            if len(df_hist) < 20:
                continue
            df_recent = df_hist.tail(20)
            
            # 거래대금 = 거래량 * 종가
            avg_amount = (df_recent['Volume'] * df_recent['Close']).mean()
            if avg_amount < 1_000_000_000: # 10억 원
                continue
        except Exception as e:
            logger.warning(f"[{corp_name}] 거래대금 조회 실패: {e}")
            continue
            
        # 2. DART 재무 필터링
        corp_code = dart.corp_code_map[stock_code]["corp_code"]
        try:
            fs = dart.get_financial_statements(stock_code, check_year, "11011", "CFS")
            if not fs:
                fs = dart.get_financial_statements(stock_code, check_year, "11011", "OFS")
                
            if not fs:
                continue
                
            revenue = 0.0
            op_income = 0.0
            total_liab = 0.0
            total_equity = 0.0
            
            for item in fs:
                nm = item.get("account_nm", "")
                amt_str = item.get("thstrm_amount", "0").replace(",", "")
                try:
                    amt = float(amt_str)
                except ValueError:
                    amt = 0.0
                    
                if "매출액" in nm or "영업수익" in nm:
                    revenue = amt
                elif "영업이익" in nm and "영업이익" == nm.strip():
                    op_income = amt
                elif "부채총계" in nm:
                    total_liab = amt
                elif "자본총계" in nm:
                    total_equity = amt
                    
            debt_ratio = (total_liab / total_equity * 100) if total_equity > 0 else 999
            
            # 강화된 선별 기준 적용 (매출 1,500억 이상, 영업이익 흑자, 부채비율 200% 미만)
            if (op_income > 0 and 
                revenue >= 150_000_000_000 and 
                debt_ratio < 200):
                
                candidates.append((stock_code, corp_name, revenue, op_income, debt_ratio, avg_amount))
                logger.info(f"✅ 유니버스 편입 후보 추가: {corp_name}({stock_code}) "
                            f"매출={revenue/1e8:.0f}억, 영업이익={op_income/1e8:.0f}억, "
                            f"부채비율={debt_ratio:.0f}%, 20일평균거래대금={avg_amount/1e8:.1f}억")
                
            checked += 1
            time.sleep(0.5) # API 호출 지연 방지
        except Exception as e:
            logger.warning(f"[{corp_name}] DART 재무제표 조회 실패: {e}")
            continue

    if candidates:
        active_universe_path = os.path.join(LOG_DIR, "active_universe.json")
        active_tickers = []
        if os.path.exists(active_universe_path):
            try:
                with open(active_universe_path, "r", encoding="utf-8") as f:
                    active_data = json.load(f)
                    active_tickers = active_data.get("tickers", [])
            except Exception as e:
                logger.error(f"기존 active_universe.json 로드 실패: {e}")

        existing_active_codes = set(item["ticker"] for item in active_tickers)
        new_additions = []

        for c in candidates:
            ticker, name, rev, op, debt, avg_amt = c
            if ticker not in existing_active_codes:
                active_tickers.append({"ticker": ticker, "name": name})
                new_additions.append(f"  · {name} ({ticker}): 매출 {rev/1e8:.0f}억 / 영익 {op/1e8:.0f}억 / 부채 {debt:.1f}%")

        # active_universe.json 업데이트 저장
        with open(active_universe_path, "w", encoding="utf-8") as f:
            json.dump({"tickers": active_tickers}, f, ensure_ascii=False, indent=2)

        # 텔레그램 알림 발송
        try:
            from stock_trader.communication.telegram_utils import send_telegram_message
            if new_additions:
                msg = (
                    f"🎉 *[유니버스 신규 종목 자동 추가]*\n"
                    f"━━━━━━━━━━━━━━\n"
                    f"📅 기준 사업연도: {check_year}\n"
                    f"🔍 신규 편입: {len(new_additions)}종목\n\n"
                    + "\n".join(new_additions[:10])
                )
                if len(new_additions) > 10:
                    msg += f"\n  ... 외 {len(new_additions)-10}종목"
                msg += "\n━━━━━━━━━━━━━━"
                send_telegram_message(msg)
                logger.info(f"🎉 유니버스 자동 편입 및 알림 전송 완료: {len(new_additions)}종목")
            else:
                msg = f"ℹ️ *[유니버스 확장 검토]*\n검토 완료: 새로 추가할 수 있는 신규 우량 종목이 없습니다."
                send_telegram_message(msg)
                logger.info("유니버스 확장 검토 완료 (신규 추가 없음)")
        except Exception as e:
            logger.error(f"텔레그램 메시지 발송 실패: {e}")
    else:
        logger.info("유니버스 확장 후보 없음")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    expand_universe()
