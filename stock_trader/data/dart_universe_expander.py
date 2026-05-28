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

logger = logging.getLogger("DartUniverseExpander")


def expand_universe():
    """DART 재무제표 기반으로 종목 유니버스를 자동 확장한다."""
    dart = DartAPI()
    
    if not dart.corp_code_map:
        logger.error("DART 고유번호 매핑이 비어 있습니다.")
        return
    
    # 기존 유니버스 로드
    from stock_trader.core.stock_universe import SAMPLE_TICKERS
    existing_tickers = set(t[0] for t in SAMPLE_TICKERS)
    
    candidates = []
    now = datetime.datetime.now()
    check_year = now.year - 1 if now.month > 3 else now.year - 2
    
    # 전체 상장사 중 KOSPI 종목을 순회
    checked = 0
    for stock_code, info in dart.corp_code_map.items():
        if stock_code in existing_tickers:
            continue  # 이미 유니버스에 있는 종목은 건너뛰기
        
        if checked >= 50:  # API 호출 제한을 위해 최대 50종목까지만 추가 검토
            break
        
        corp_code = info["corp_code"]
        corp_name = info["corp_name"]
        
        try:
            fs = dart.get_financial_statements(stock_code, check_year, "11011", "CFS")
            if not fs:
                fs = dart.get_financial_statements(stock_code, check_year, "11011", "OFS")
            
            if not fs:
                continue
            
            # 재무제표에서 핵심 지표 추출
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
            
            # 선별 기준 적용
            debt_ratio = (total_liab / total_equity * 100) if total_equity > 0 else 999
            
            if (op_income > 0 and           # 영업이익 흑자
                revenue >= 100_000_000_000 and  # 매출 1,000억 이상
                debt_ratio < 300):           # 부채비율 300% 미만
                
                candidates.append((stock_code, corp_name))
                logger.info(f"✅ 유니버스 편입 후보: {corp_name}({stock_code}) "
                           f"매출={revenue/1e8:.0f}억, 영업이익={op_income/1e8:.0f}억, "
                           f"부채비율={debt_ratio:.0f}%")
            
            checked += 1
            import time
            time.sleep(0.5)  # API 호출 제한 준수
            
        except Exception as e:
            logger.warning(f"[{corp_name}] 재무제표 조회 실패: {e}")
            continue
    
    if candidates:
        # stock_universe.py 자동 갱신은 위험하므로, 후보 목록을 JSON으로 저장하여 수동 검토를 유도한다
        output_path = os.path.join(BASE_DIR, "logs", "universe_candidates.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "generated_at": datetime.datetime.now().isoformat(),
                "check_year": check_year,
                "candidates": [{"ticker": t, "name": n} for t, n in candidates],
                "count": len(candidates)
            }, f, ensure_ascii=False, indent=2)
        
        logger.info(f"📋 유니버스 확장 후보 {len(candidates)}개 저장 완료: {output_path}")
        
        # 텔레그램 알림
        try:
            from stock_trader.communication.telegram_utils import send_telegram_message
            msg = (
                f"📋 *[유니버스 확장 후보]*\n"
                f"━━━━━━━━━━━━━━\n"
                f"📅 기준 사업연도: {check_year}\n"
                f"🔍 신규 후보: {len(candidates)}종목\n\n"
            )
            for t, n in candidates[:10]:  # 상위 10개만 표시
                msg += f"  · {n} ({t})\n"
            if len(candidates) > 10:
                msg += f"  ... 외 {len(candidates)-10}종목\n"
            msg += "\n━━━━━━━━━━━━━━"
            send_telegram_message(msg)
        except Exception:
            pass
    else:
        logger.info("유니버스 확장 후보 없음")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
    expand_universe()
