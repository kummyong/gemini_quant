"""
DART 재무제표 기반 팩터 점수 계산기
──────────────────────────────────
DART API에서 조회한 확정 재무제표를 분석하여 4개의 서브 팩터 점수를 산출한다.
strategy_engine.py의 calculate_scores()에서 호출된다.
"""
import logging
import datetime
from typing import Dict, Optional
from dart_api import DartAPI

logger = logging.getLogger("DartFinancialScorer")


class DartFinancialScorer:
    def __init__(self, dart_api: DartAPI):
        self.dart = dart_api
        self._cache = {}  # {stock_code: score_dict} — 하루 1회만 조회하므로 메모리 캐시 충분
    
    def get_financial_score(self, stock_code: str) -> Dict[str, float]:
        """
        해당 종목의 DART 재무제표 기반 점수를 반환한다.
        
        Returns:
            dict with keys:
                - "revenue_growth": 매출 성장률 (%, 전년 대비)
                - "op_profit_growth": 영업이익 성장률 (%, 전년 대비)
                - "debt_ratio": 부채비율 (%, 부채총계/자본총계 × 100)
                - "cash_flow_quality": 현금흐름 품질 점수 (0~100)
                - "dart_available": True/False — DART 데이터 조회 성공 여부
        """
        if stock_code in self._cache:
            return self._cache[stock_code]
        
        default_score = {
            "revenue_growth": 0.0,
            "op_profit_growth": 0.0,
            "debt_ratio": 100.0,
            "cash_flow_quality": 50.0,
            "dart_available": False
        }
        
        try:
            now = datetime.datetime.now()
            # 현재 연도와 전년도의 사업보고서를 조회한다.
            # 사업보고서(11011)는 보통 3월에 제출되므로, 1~3월에는 전전년도를 사용한다.
            if now.month <= 3:
                current_year = now.year - 2
            else:
                current_year = now.year - 1
            
            prev_year = current_year - 1
            
            # 당기(최신) 재무제표 조회
            current_fs = self.dart.get_financial_statements(stock_code, current_year, "11011", "CFS")
            if not current_fs:
                # 연결재무제표가 없으면 개별재무제표 시도
                current_fs = self.dart.get_financial_statements(stock_code, current_year, "11011", "OFS")
            
            # 전기 재무제표 조회
            prev_fs = self.dart.get_financial_statements(stock_code, prev_year, "11011", "CFS")
            if not prev_fs:
                prev_fs = self.dart.get_financial_statements(stock_code, prev_year, "11011", "OFS")
            
            if not current_fs:
                logger.info(f"ℹ️ [{stock_code}] DART 재무제표 조회 결과 없음 — 기본값 사용")
                self._cache[stock_code] = default_score
                return default_score
            
            # 재무제표 항목에서 금액 추출 헬퍼
            def extract_amount(fs_list, account_keywords):
                """재무제표 리스트에서 특정 계정명과 매칭되는 항목의 당기금액을 추출한다."""
                for item in fs_list:
                    account_nm = item.get("account_nm", "")
                    for keyword in account_keywords:
                        if keyword in account_nm:
                            amt_str = item.get("thstrm_amount", "0").replace(",", "")
                            try:
                                return float(amt_str)
                            except ValueError:
                                return 0.0
                return 0.0
            
            # ──── 1. 매출 성장률 계산 ────
            cur_revenue = extract_amount(current_fs, ["매출액", "수익(매출액)", "영업수익"])
            prev_revenue = extract_amount(prev_fs, ["매출액", "수익(매출액)", "영업수익"]) if prev_fs else 0.0
            
            revenue_growth = 0.0
            if prev_revenue and prev_revenue != 0:
                revenue_growth = ((cur_revenue - prev_revenue) / abs(prev_revenue)) * 100
            
            # ──── 2. 영업이익 성장률 계산 ────
            cur_op = extract_amount(current_fs, ["영업이익", "영업손익"])
            prev_op = extract_amount(prev_fs, ["영업이익", "영업손익"]) if prev_fs else 0.0
            
            op_profit_growth = 0.0
            if prev_op and prev_op != 0:
                op_profit_growth = ((cur_op - prev_op) / abs(prev_op)) * 100
            
            # ──── 3. 부채비율 계산 ────
            total_liabilities = extract_amount(current_fs, ["부채총계"])
            total_equity = extract_amount(current_fs, ["자본총계"])
            
            debt_ratio = 100.0
            if total_equity and total_equity > 0:
                debt_ratio = (total_liabilities / total_equity) * 100
            
            # ──── 4. 현금흐름 품질 점수 ────
            # 영업활동 현금흐름이 순이익보다 크면 어닝 퀄리티가 높다고 판단
            operating_cf = extract_amount(current_fs, ["영업활동으로인한현금흐름", "영업활동 현금흐름", "영업활동현금흐름"])
            net_income = extract_amount(current_fs, ["당기순이익", "당기순손익"])
            
            cash_flow_quality = 50.0  # 기본값
            if net_income and net_income > 0:
                cf_ratio = operating_cf / net_income
                # cf_ratio > 1.0 이면 현금흐름 품질 좋음 → 높은 점수
                # cf_ratio < 0.5 이면 어닝 퀄리티 의심 → 낮은 점수
                cash_flow_quality = min(100.0, max(0.0, cf_ratio * 50.0))
            
            score = {
                "revenue_growth": round(revenue_growth, 2),
                "op_profit_growth": round(op_profit_growth, 2),
                "debt_ratio": round(debt_ratio, 2),
                "cash_flow_quality": round(cash_flow_quality, 2),
                "dart_available": True
            }
            
            logger.info(
                f"📊 [{stock_code}] DART 재무 점수: "
                f"매출성장={score['revenue_growth']:.1f}%, "
                f"영업이익성장={score['op_profit_growth']:.1f}%, "
                f"부채비율={score['debt_ratio']:.1f}%, "
                f"현금흐름={score['cash_flow_quality']:.1f}점"
            )
            
            self._cache[stock_code] = score
            return score
            
        except Exception as e:
            logger.error(f"❌ [{stock_code}] DART 재무 점수 계산 중 오류: {e}")
            self._cache[stock_code] = default_score
            return default_score
    
    def get_dividend_yield(self, stock_code: str) -> float:
        """배당수익률을 조회한다. 조회 실패 시 0.0을 반환한다."""
        try:
            now = datetime.datetime.now()
            year = now.year - 1 if now.month <= 3 else now.year - 1
            
            div_data = self.dart.get_dividend_info(stock_code, year)
            if not div_data:
                return 0.0
            
            # "현금배당수익률" 항목에서 보통주 배당수익률 추출
            for item in div_data:
                se_nm = item.get("se", "")
                if "현금배당수익률" in se_nm or "배당수익률" in se_nm:
                    # stock_knd가 "보통주"인 항목을 찾는다
                    stock_knd = item.get("stock_knd", "")
                    if "보통주" in stock_knd or stock_knd == "":
                        thstrm = item.get("thstrm", "0").replace(",", "").replace("-", "0")
                        try:
                            return float(thstrm)
                        except ValueError:
                            return 0.0
            return 0.0
        except Exception as e:
            logger.error(f"❌ [{stock_code}] 배당수익률 조회 오류: {e}")
            return 0.0
    
    def get_major_shareholder_signal(self, stock_code: str) -> float:
        """
        대량보유(5% 룰) 지분변동 기반 수급 보너스 점수를 반환한다.
        
        Returns:
            float: -10.0 ~ +15.0 범위의 보너스 점수
        """
        try:
            reports = self.dart.get_major_shareholders(stock_code)
            if not reports:
                return 0.0
            
            # 최근 보고서의 보유 비율 변동 확인
            # 보고서는 최신순으로 정렬되어 있다고 가정
            latest = reports[0] if reports else None
            if not latest:
                return 0.0
            
            # reprt_detail_ty: 보고 유형 (신규, 변동)
            report_type = latest.get("reprt_detail_ty", "")
            
            # stkqy_irds_rate: 주식 증감 비율
            change_rate_str = latest.get("stkqy_irds_rate", "0").replace(",", "").replace("-", "0")
            try:
                change_rate = float(change_rate_str)
            except ValueError:
                change_rate = 0.0
            
            if "신규" in report_type:
                return 15.0  # 신규 대량보유 → 강한 매수 신호
            elif change_rate > 1.0:
                return 10.0  # 1%p 이상 추가 매수
            elif change_rate < -1.0:
                return -10.0  # 지분 감소 → 경계
            
            return 0.0
        except Exception as e:
            logger.error(f"❌ [{stock_code}] 대량보유 시그널 조회 오류: {e}")
            return 0.0
