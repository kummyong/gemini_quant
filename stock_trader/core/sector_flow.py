"""섹터 수급 로테이션 신호.

Kiwoom ka10051(업종별 투자자 순매수) + ka20003(전업종지수)를 일 1회 수집해
sector_flows/sector_prices에 적재하고, 과거 이력으로부터 세 가지 신호를
자기 과거 분포 대비 z-score로 정규화한 뒤 가중합해 섹터를 랭킹한다.

- 수급 가속도: 20일 누적(외국인+기관) 순매수의 변화량
- RS 변곡: (섹터지수/종합지수) 비율의 20일 선형회귀 기울기
- Breadth: 상승종목비율(rising/전체)의 5일 평활값
"""
import argparse
import datetime
import logging

import numpy as np
import pandas as pd

from stock_trader.config import DB_PATH
from stock_trader.data.db_repository import DbRepository
from stock_trader.broker.kiwoom.kiwoom_api_core import KiwoomApiCore
from stock_trader.core.korean_market_calendar import is_market_holiday

logger = logging.getLogger("SectorFlow")

# 신호 가중치 (튜닝 가능 — scorer.py의 WEIGHT_* 상수와 동일한 패턴)
WEIGHT_FLOW_ACCEL = 0.40
WEIGHT_RS_SLOPE = 0.35
WEIGHT_BREADTH = 0.25

MRKT_TYPES = ("0", "1")  # 0=코스피, 1=코스닥
INDEX_CODES = ("001", "101")  # 001=코스피 종합, 101=코스닥 종합
KOSPI_COMPOSITE_CD = "001"
KOSDAQ_COMPOSITE_CD = "101"

ACCEL_WINDOW = 20
LOOKBACK_DAYS = 90


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _normalize_inds_cd(raw_cd: str) -> str:
    """ka10051은 통합조회(stex_tp=3) 시 '001_AL'처럼 시장접미사가 붙어 나온다.
    ka20003의 순수 코드('001')와 조인 키를 맞추기 위해 접미사를 제거한다."""
    return (raw_cd or "").split("_")[0]


def collect_daily(mode: str = "MOCK", target_date: str = None) -> int:
    """ka10051(코스피/코스닥)·ka20003(코스피/코스닥) 총 4콜을 호출해 DB에 적재한다.
    반환값: 적재된 업종 수급 row 개수."""
    date_str = target_date or datetime.date.today().strftime("%Y%m%d")
    api = KiwoomApiCore(mode=mode)
    repo = DbRepository(DB_PATH)

    saved = 0
    for mrkt_tp in MRKT_TYPES:
        resp = api.get_sector_investor_netbuy(mrkt_tp, base_dt=date_str)
        if not resp or resp.get("return_code") != 0:
            logger.error(f"❌ ka10051(mrkt_tp={mrkt_tp}) 조회 실패: {resp}")
            continue
        for row in resp.get("inds_netprps", []):
            repo.save_sector_flow(
                date=date_str,
                inds_cd=_normalize_inds_cd(row.get("inds_cd", "")),
                inds_nm=row.get("inds_nm", ""),
                mrkt_tp=mrkt_tp,
                frgnr_netprps=_to_float(row.get("frgnr_netprps")),
                orgn_netprps=_to_float(row.get("orgn_netprps")),
                ind_netprps=_to_float(row.get("ind_netprps")),
            )
            saved += 1

    for inds_cd in INDEX_CODES:
        resp = api.get_sector_index(inds_cd)
        if not resp or resp.get("return_code") != 0:
            logger.error(f"❌ ka20003(inds_cd={inds_cd}) 조회 실패: {resp}")
            continue
        for row in resp.get("all_inds_idex", []):
            repo.save_sector_price(
                date=date_str,
                inds_cd=_normalize_inds_cd(row.get("stk_cd", "")),
                inds_nm=row.get("stk_nm", ""),
                cur_prc=_to_float(row.get("cur_prc")),
                flu_rt=_to_float(row.get("flu_rt")),
                rising=int(_to_float(row.get("rising"))),
                stdns=int(_to_float(row.get("stdns"))),
                fall=int(_to_float(row.get("fall"))),
            )

    logger.info(f"✅ 섹터 수급/지수 수집 완료 ({date_str}): {saved}개 업종 순매수 적재")
    return saved


def _zscore(latest: float, history: list) -> float:
    """history(자기 과거 분포) 대비 latest의 z-score.
    표본이 부족(5개 미만)하거나 분산이 0이면 0.0(무보정)을 반환한다."""
    if len(history) < 5:
        return 0.0
    arr = np.array(history, dtype=float)
    std = arr.std()
    if std < 1e-9:
        return 0.0
    return float((latest - arr.mean()) / std)


def _slope(values: list) -> float:
    """values(순서대로)에 대한 선형회귀 기울기."""
    if len(values) < 3:
        return 0.0
    y = np.array(values, dtype=float)
    x = np.arange(len(y))
    slope, _ = np.polyfit(x, y, 1)
    return float(slope)


def compute_rankings(lookback_days: int = LOOKBACK_DAYS, accel_window: int = ACCEL_WINDOW) -> list:
    """업종별 수급가속도/RS변곡/breadth를 z-score로 합성해 랭킹을 반환한다.
    이력이 부족한 업종(신규 상장/최근 수집 시작 등)은 조용히 스킵한다.

    반환: composite_z 내림차순 리스트.
    [{"inds_cd", "inds_nm", "mrkt_tp", "z_flow_accel", "z_rs_slope", "z_breadth", "composite_z"}, ...]
    """
    repo = DbRepository(DB_PATH)
    sectors = repo.get_all_sector_codes()
    if not sectors:
        logger.warning("⚠️ sector_flows에 적재된 데이터가 없습니다. collect_daily()를 먼저 실행하세요.")
        return []

    kospi_hist = repo.get_sector_price_history(KOSPI_COMPOSITE_CD, days=lookback_days)
    kosdaq_hist = repo.get_sector_price_history(KOSDAQ_COMPOSITE_CD, days=lookback_days)
    composite_by_mrkt = {
        "0": {r["date"]: r["cur_prc"] for r in kospi_hist},
        "1": {r["date"]: r["cur_prc"] for r in kosdaq_hist},
    }

    results = []
    for sec in sectors:
        inds_cd = sec["inds_cd"]
        if inds_cd in (KOSPI_COMPOSITE_CD, KOSDAQ_COMPOSITE_CD):
            continue  # 종합지수 자신은 랭킹 대상이 아니라 RS 기준선으로만 쓴다

        flow_hist = repo.get_sector_flow_history(inds_cd, days=lookback_days)
        price_hist = repo.get_sector_price_history(inds_cd, days=lookback_days)
        if len(flow_hist) < accel_window * 2 or len(price_hist) < accel_window + 5:
            continue

        mrkt_tp = flow_hist[-1]["mrkt_tp"]
        inds_nm = flow_hist[-1]["inds_nm"]

        # 1. 수급 가속도: 20일 누적(외국인+기관) 순매수의 변화량 시계열 → 최신값의 z-score
        net_flow = [r["frgnr_netprps"] + r["orgn_netprps"] for r in flow_hist]
        cum20 = pd.Series(net_flow).rolling(accel_window).sum().dropna().tolist()
        if len(cum20) <= accel_window:
            continue
        accel_series = [cum20[i] - cum20[i - accel_window] for i in range(accel_window, len(cum20))]
        z_flow_accel = _zscore(accel_series[-1], accel_series[:-1])

        # 2. RS 변곡: (섹터지수/종합지수) 비율의 20일 기울기 → 최신 기울기의 z-score
        comp_map = composite_by_mrkt.get(mrkt_tp, {})
        ratio_series = [
            r["cur_prc"] / comp_map[r["date"]]
            for r in price_hist if comp_map.get(r["date"]) and r["cur_prc"]
        ]
        if len(ratio_series) < accel_window + 5:
            continue
        slopes = [_slope(ratio_series[i - accel_window:i]) for i in range(accel_window, len(ratio_series) + 1)]
        z_rs_slope = _zscore(slopes[-1], slopes[:-1])

        # 3. Breadth: 상승비율(rising / 전체)의 5일 평활 → z-score
        breadth_series = []
        for r in price_hist:
            total = (r["rising"] or 0) + (r["stdns"] or 0) + (r["fall"] or 0)
            breadth_series.append((r["rising"] or 0) / total if total > 0 else 0.5)
        breadth_smoothed = pd.Series(breadth_series).rolling(5).mean().dropna().tolist()
        if not breadth_smoothed:
            continue
        z_breadth = _zscore(breadth_smoothed[-1], breadth_smoothed[:-1])

        composite_z = (
            WEIGHT_FLOW_ACCEL * z_flow_accel +
            WEIGHT_RS_SLOPE * z_rs_slope +
            WEIGHT_BREADTH * z_breadth
        )

        results.append({
            "inds_cd": inds_cd, "inds_nm": inds_nm, "mrkt_tp": mrkt_tp,
            "z_flow_accel": round(z_flow_accel, 3), "z_rs_slope": round(z_rs_slope, 3),
            "z_breadth": round(z_breadth, 3), "composite_z": round(composite_z, 3),
        })

    results.sort(key=lambda x: x["composite_z"], reverse=True)
    return results


def get_flow_score_map() -> dict:
    """scorer.apply_sector_momentum()이 소비하는 {inds_nm: composite_z} 딕셔너리를 반환한다."""
    return {r["inds_nm"]: r["composite_z"] for r in compute_rankings() if r.get("inds_nm")}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    parser = argparse.ArgumentParser(description="섹터 수급 로테이션 신호 수집/랭킹")
    parser.add_argument("--collect", action="store_true", help="당일 업종 수급/지수 데이터를 수집해 DB에 적재")
    parser.add_argument("--rank", action="store_true", help="현재 DB 이력 기준 섹터 랭킹을 출력")
    parser.add_argument("--mode", default="MOCK", help="Kiwoom API 모드 (MOCK/REAL)")
    args = parser.parse_args()

    if args.collect:
        now = datetime.datetime.now()
        if is_market_holiday(now):
            logger.info("휴장일이라 섹터 수급 수집을 건너뜁니다.")
        else:
            collect_daily(mode=args.mode)

    if args.rank or not (args.collect or args.rank):
        rankings = compute_rankings()
        print(f"\n{'업종코드':<8}{'업종명':<20}{'수급가속':>10}{'RS변곡':>10}{'Breadth':>10}{'합성점수':>10}")
        for r in rankings[:15]:
            print(f"{r['inds_cd']:<8}{r['inds_nm']:<20}{r['z_flow_accel']:>10.2f}"
                  f"{r['z_rs_slope']:>10.2f}{r['z_breadth']:>10.2f}{r['composite_z']:>10.2f}")
