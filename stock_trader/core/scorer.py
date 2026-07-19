"""멀티팩터 스코어러.

strategy_engine.calculate_scores()의 팩터 정규화/가중합 스코어링과
fetch_market_data() 후반부의 주간(5일) 섹터 모멘텀 계산을 분리한 모듈.
외부 I/O 없이 종목 딕셔너리 리스트만 입력받아 동작한다.
"""
import logging
import re
from typing import Dict, List

import stock_trader.config as trader_config

logger = logging.getLogger("MultiFactorScorer")

# ── 기존 팩터 가중치 (네이버 금융 기반) ──
WEIGHT_EARNINGS = 0.15       # 기존 EPS 성장률 (네이버 금융)
WEIGHT_MACRO = 0.20          # 산업 트렌드/매크로

# ── DART 팩터 가중치 (공시 재무제표 기반) ──
WEIGHT_DART_REVENUE = 0.15   # DART: 매출 성장률
WEIGHT_DART_OP_PROFIT = 0.20 # DART: 영업이익 성장률
WEIGHT_DART_HEALTH = 0.05    # DART: 재무건전성 (부채비율/현금흐름)
WEIGHT_INSTITUTIONAL = 0.25  # 수급(기관/외인) — 대량보유 보너스 포함

# 합계: 0.15 + 0.20 + 0.15 + 0.20 + 0.05 + 0.25 = 1.00

# 스마트 머니(수급) 가점 합산 상한 — 내부자 매수(+10)와 연속매집(+7)이 겹쳐도
# 기존 기술 보너스 한 개(VCP +10, 상대모멘텀 +10)를 약간 넘는 수준까지만 허용한다.
SMART_MONEY_BONUS_CAP = 12.0


def apply_sector_momentum(stocks: List[Dict]) -> None:
    """주간(5일) 섹터 모멘텀을 계산하여 각 종목의 industry_score를 갱신한다(in-place)."""
    try:
        industry_groups = {}
        for s in stocks:
            ind_name = s["industry_name"]
            if ind_name not in industry_groups:
                industry_groups[ind_name] = []
            industry_groups[ind_name].append(s["return_5d"])

        industry_momentum = {}
        for ind_name, returns in industry_groups.items():
            industry_momentum[ind_name] = sum(returns) / len(returns)

        for s in stocks:
            ind_name = s["industry_name"]
            avg_ret_5d = industry_momentum.get(ind_name, 0.0)
            score = 57.5 + avg_ret_5d * 5.0
            s["industry_score"] = max(20.0, min(95.0, score))
            logger.info(f"📂 [{s['name']}] 업종: {ind_name} | 5일 섹터 모멘텀: {avg_ret_5d:+.2f}% | 업종 점수: {s['industry_score']:.1f}")
    except Exception as sec_e:
        logger.error(f"주간 섹터 모멘텀 계산 오류: {sec_e}")


def _normalize_sector_name(name: str) -> str:
    """업종명 비교용 정규화: 공백/특수문자 제거."""
    return re.sub(r"[^0-9A-Za-z가-힣]", "", name or "")


def apply_sector_flow_score(stocks: List[Dict], flow_score_map: Dict[str, float]) -> None:
    """섹터 수급 로테이션 신호(core/sector_flow.py의 composite_z)를 industry_score에 가산 보정한다(in-place).

    flow_score_map은 {Kiwoom 업종명: composite_z}. 네이버 크롤링 industry_name과 Kiwoom 업종명은
    분류 체계가 달라 정확히 일치하지 않을 수 있으므로 정규화 후 정확일치 → 부분일치 순으로 매칭하고,
    끝내 매칭에 실패한 종목은 조용히 건너뛴다(기존 가격 모멘텀 기반 industry_score를 그대로 유지).
    실거래 파이프라인이므로 매핑 실패가 스코어링 자체를 중단시켜서는 안 된다.
    """
    if not flow_score_map:
        return
    normalized_map = {_normalize_sector_name(k): v for k, v in flow_score_map.items() if k}
    matched, unmatched = 0, 0
    for s in stocks:
        ind_name = _normalize_sector_name(s.get("industry_name", ""))
        if not ind_name:
            continue
        flow_z = normalized_map.get(ind_name)
        if flow_z is None:
            for norm_kiwoom_name, z in normalized_map.items():
                if norm_kiwoom_name and (norm_kiwoom_name in ind_name or ind_name in norm_kiwoom_name):
                    flow_z = z
                    break
        if flow_z is None:
            unmatched += 1
            continue
        matched += 1
        adjustment = max(-10.0, min(10.0, flow_z * 5.0))
        base_score = s.get("industry_score", 57.5)
        s["industry_score"] = max(20.0, min(95.0, base_score + adjustment))
    logger.info(f"📊 섹터 수급 점수 병합: {matched}개 종목 매칭, {unmatched}개 미매칭(가격모멘텀만 반영)")


class MultiFactorScorer:
    """네이버(EPS/수급) + DART(재무) + 기술적 보너스/페널티 기반 멀티팩터 스코어러"""

    def calculate_scores(self, stocks: List[Dict]) -> List[Dict]:
        if not stocks: return []
        eps_values = [s["eps_growth"] for s in stocks]
        net_values = [s["net_buying"] for s in stocks]
        rev_values = [s.get("dart_revenue_growth", 0.0) for s in stocks]
        op_values = [s.get("dart_op_growth", 0.0) for s in stocks]

        min_eps, max_eps = min(eps_values), max(eps_values)
        min_net, max_net = min(net_values), max(net_values)
        min_rev, max_rev = min(rev_values), max(rev_values)
        min_op, max_op = min(op_values), max(op_values)

        eps_range = max_eps - min_eps if max_eps != min_eps else 1.0
        net_range = max_net - min_net if max_net != min_net else 1.0
        rev_range = max_rev - min_rev if max_rev != min_rev else 1.0
        op_range = max_op - min_op if max_op != min_op else 1.0

        for stock in stocks:
            s_eps = ((stock["eps_growth"] - min_eps) / eps_range) * 100
            s_macro = stock["industry_score"]
            s_rev = ((stock.get("dart_revenue_growth", 0.0) - min_rev) / rev_range) * 100
            s_op = ((stock.get("dart_op_growth", 0.0) - min_op) / op_range) * 100
            debt_score = max(0, 100 - stock.get("dart_debt_ratio", 100.0))
            health_score = (debt_score * 0.5 + stock.get("dart_cf_quality", 50.0) * 0.5)
            s_net = ((stock["net_buying"] - min_net) / net_range) * 100

            final_score = (
                s_eps * WEIGHT_EARNINGS +
                s_macro * WEIGHT_MACRO +
                s_rev * WEIGHT_DART_REVENUE +
                s_op * WEIGHT_DART_OP_PROFIT +
                health_score * WEIGHT_DART_HEALTH +
                s_net * WEIGHT_INSTITUTIONAL
            )

            final_score += stock.get("dart_major_shareholder_bonus", 0.0)
            div_yield = stock.get("dart_dividend_yield", 0.0)
            if div_yield >= 3.0:
                final_score += min(8.0, div_yield * 2.0)
            if stock.get("dart_debt_ratio", 100.0) > 200:
                final_score -= 10.0

            # 상대 모멘텀(Relative Strength) 보너스 반영 (최대 10점)
            rel_mom = stock.get("relative_momentum", 0.0)
            if rel_mom > 0:
                final_score += min(10.0, rel_mom * 0.1)

            # 120일 이평선 하회(장기 역배열) 감점
            if stock.get("is_under_ma120", False):
                final_score -= 15.0
            # 20-60-120일 정배열 가점
            elif stock.get("is_aligned", False):
                final_score += 5.0

            # BB 하단 부근 거래량 급감 (VCP) 가점
            if stock.get("is_vcp", False):
                final_score += 10.0
                logger.info(f"✨ [{stock['name']}] BB 하단 거래량 급감 (VCP 패턴) 포착! 가점 10점 부여")

            # 스마트 머니(수급) 매집 보너스: 내부자 장내매수 + 외인/기관 연속 순매수/OBV.
            # 가점 크기는 기존 기술 보너스(VCP +10, 상대모멘텀 최대 +10)와 같은 급으로 제한하고,
            # 합산 캡(SMART_MONEY_BONUS_CAP)으로 수급 계열이 가중합 팩터 모델을 지배하지 못하게 한다.
            # 예측력 검증은 진입 스냅샷 → trade_outcomes → factor_analysis(IC) 경로로 계속 축적된다.
            if getattr(trader_config, 'ENABLE_SMART_MONEY_BONUS', False):
                consec_days = stock.get("consecutive_buy_days", 0)
                obv_rising = stock.get("is_obv_rising", False)
                has_insider_buying = stock.get("has_insider_buying", False)

                smart_money_bonus = 0.0
                # 1. 내부자(임원/주요주주) 장내매수 발생 (확신 매수 — 가장 강한 시그널)
                if has_insider_buying:
                    smart_money_bonus += 10.0
                    logger.info(f"💎 [{stock['name']}] 내부자 장내매수 포착 (스마트 머니)! 가점 10점 부여")

                # 2. 외인/기관 3일 이상 연속 순매수 & OBV 단기 상향 추세
                if consec_days >= 3 and obv_rising:
                    smart_money_bonus += 7.0
                    logger.info(f"🔥 [{stock['name']}] 스마트 머니 연속 매집 포착 ({consec_days}일 연속 순매수 + OBV 우상향)! 가점 7점 부여")
                # 3. OBV는 약하더라도 연속 순매수 자체가 4일 이상이면 가점
                elif consec_days >= 4:
                    smart_money_bonus += 4.0
                    logger.info(f"💧 [{stock['name']}] 외인/기관 연속 매집 ({consec_days}일)! 가점 4점 부여")

                final_score += min(SMART_MONEY_BONUS_CAP, smart_money_bonus)

            # 센티먼트/미시구조 보너스 — 종가 매매(15:00)로 스케줄이 변경되었으므로
            # 장중 실시간 지표(호가잔량/체결강도/당일 게시글 수)를 적극 반영한다.
            if getattr(trader_config, 'ENABLE_SENTIMENT_MICRO_BONUS', True):
                # 4. 종목토론방 센티먼트 (조용한 매집 vs 개인 쏠림)
                if "discussion_traffic" in stock:
                    traffic = stock["discussion_traffic"]
                    net_buying_val = stock.get("net_buying", 0.0)
                    
                    if traffic <= 5 and net_buying_val > 0:
                        final_score += 5.0
                        logger.info(f"🤫 [{stock['name']}] 조용한 매집 포착 (게시글 {traffic}건, 수급유입)! 가점 5점 부여")
                    elif traffic >= 50 and net_buying_val <= 0:
                        final_score -= 10.0
                        logger.warning(f"⚠️ [{stock['name']}] 개인 쏠림(Retail Frenzy) 경고 (게시글 {traffic}건, 수급이탈)! 감점 10점 부여")

                # 5. 미시구조 지표 (체결강도 및 호가 잔량 불균형 OBI)
                if "volume_strength" in stock and "ask_volume" in stock and "bid_volume" in stock:
                    vs = stock["volume_strength"]
                    ask_v = stock["ask_volume"]
                    bid_v = stock["bid_volume"]
                    
                    # 체결강도 가점: 100% 이상이면 매수세 우위 (최대 10점)
                    if vs > 100.0:
                        vs_bonus = min(10.0, (vs - 100.0) * 0.2)
                        final_score += vs_bonus
                        logger.info(f"💪 [{stock['name']}] 강한 체결강도 ({vs}%) 포착! 가점 {vs_bonus:.2f}점 부여")
                    
                    # 호가 잔량 불균형(OBI): 한국 시장 특성상 매도 잔량이 매수 잔량보다 많으면 상승 신호.
                    if ask_v > 0 and bid_v > 0:
                        if ask_v >= bid_v * 1.5:
                            final_score += 8.0
                            logger.info(f"🧱 [{stock['name']}] 매도 호가벽 형성(매도잔량 우위)! 상승 돌파 기대 가점 8점 부여")
                        elif bid_v >= ask_v * 1.5:
                            final_score -= 5.0
                            logger.warning(f"📉 [{stock['name']}] 매수 호가벽 빽빽함(하방 압력)! 감점 5점 부여")

            stock["total_score"] = round(final_score, 2)

        return sorted(stocks, key=lambda x: x["total_score"], reverse=True)
