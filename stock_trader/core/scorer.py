"""멀티팩터 스코어러.

strategy_engine.calculate_scores()의 팩터 정규화/가중합 스코어링과
fetch_market_data() 후반부의 주간(5일) 섹터 모멘텀 계산을 분리한 모듈.
외부 I/O 없이 종목 딕셔너리 리스트만 입력받아 동작한다.
"""
import logging
import re
from typing import Dict, List

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

            stock["total_score"] = round(final_score, 2)

        return sorted(stocks, key=lambda x: x["total_score"], reverse=True)
