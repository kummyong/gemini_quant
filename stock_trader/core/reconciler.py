import os
import logging
import datetime
from logging.handlers import RotatingFileHandler
from typing import Dict, List, Optional, Any

import pytz

from stock_trader.config import LOG_DIR
from stock_trader.communication.telegram_utils import send_telegram_message

LOG_PATH = os.path.join(LOG_DIR, "reconciler.log")

# 계좌가 완전히 비어있는데 DB에 이만큼 이상의 포지션이 남아있으면 "계좌 리셋"으로 간주
ACCOUNT_RESET_MIN_POSITIONS = 2


def kst_converter(*args):
    return datetime.datetime.now(pytz.timezone('Asia/Seoul')).timetuple()


logging.Formatter.converter = kst_converter

if not os.path.exists(LOG_DIR):
    os.makedirs(LOG_DIR)

_log_formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
_file_handler = RotatingFileHandler(LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8')
_file_handler.setFormatter(_log_formatter)
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_log_formatter)

logger = logging.getLogger("Reconciler")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    logger.addHandler(_file_handler)
    logger.addHandler(_stream_handler)
    logger.propagate = False


def _notify(message: str, notify: bool = True):
    logger.warning(message)
    if not notify:
        return
    try:
        send_telegram_message(message)
    except Exception as e:
        logger.warning(f"⚠️ [Reconciler] 텔레그램 알림 전송 실패: {e}")


def _parse_broker_holdings(account_summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """브로커 API의 acnt_evlt_remn_indv_tot 응답을 ticker -> 보유정보 형태로 정규화합니다."""
    holdings: Dict[str, Dict[str, Any]] = {}
    items = (account_summary or {}).get("acnt_evlt_remn_indv_tot") or []
    for item in items:
        try:
            ticker = str(item.get("stk_cd", "")).replace("A", "")
            if not ticker:
                continue
            qty = int(item.get("rmnd_qty", 0))
            if qty <= 0:
                continue
            avg_price = float(item.get("pchs_amt", 0.0)) / max(1, qty)
            cur_price = float(item.get("evlt_amt", 0.0)) / max(1, qty)
            prft_rt_raw = item.get("prft_rt", 0.0)
            prft_rt = float(prft_rt_raw) if prft_rt_raw not in (None, "") else 0.0
            holdings[ticker] = {
                "name": item.get("stk_nm", ticker),
                "quantity": qty,
                "avg_price": avg_price,
                "current_price": cur_price,
                "profit_rate": prft_rt,
            }
        except Exception as e:
            logger.warning(f"⚠️ [Reconciler] 보유 종목 파싱 실패 (항목 스킵): {item} ({e})")
    return holdings


def reconcile(broker_id: str, broker, repo, notify: bool = True) -> Dict[str, Any]:
    """브로커 실계좌(진실)와 DB 포지션 기록(사본)을 대사합니다.

    전략 상태(peak_close 기반이 되는 max_profit_rate, stopped_positions, market_lockout)는
    브로커가 알 수 없는 데이터이므로 이 함수는 절대 덮어쓰지 않는다.

    반환값:
        {
            "success": bool,               # 대사가 정상적으로 수행되었는지 (API/DB 오류 없었는지)
            "error": Optional[str],        # 실패 사유
            "ghost_positions": List[str],   # DB에만 있어 정리된 티커
            "unrecorded_positions": List[str], # 계좌에만 있어 DB에 신규 등록된 티커
            "mismatched_positions": List[str], # 수량/단가 불일치로 갱신된 티커
            "account_reset_detected": bool  # 모의계좌 리셋 감지 여부
        }
    """
    result: Dict[str, Any] = {
        "success": False,
        "error": None,
        "ghost_positions": [],
        "unrecorded_positions": [],
        "mismatched_positions": [],
        "account_reset_detected": False,
    }

    try:
        account_summary = broker.get_account_summary()
    except Exception as e:
        result["error"] = str(e)
        _notify(f"❌ [Reconciler][{broker_id}] 계좌 조회 실패로 포지션 대사를 중단합니다: {e}", notify)
        return result

    if account_summary is None:
        result["error"] = "empty account summary"
        _notify(f"❌ [Reconciler][{broker_id}] 계좌 조회 응답이 비어있어 포지션 대사를 중단합니다.", notify)
        return result

    try:
        db_rows = repo.get_portfolio_holdings()
    except Exception as e:
        result["error"] = str(e)
        _notify(f"❌ [Reconciler][{broker_id}] DB 포지션 조회 실패로 포지션 대사를 중단합니다: {e}", notify)
        return result

    broker_holdings = _parse_broker_holdings(account_summary)
    db_holdings = {row["stk_cd"]: row for row in db_rows if row.get("broker_id") == broker_id}

    # 시나리오 d) 계좌 리셋 감지: 계좌는 완전히 비어있는데 DB에 다수 포지션이 남아있음
    if not broker_holdings and len(db_holdings) >= ACCOUNT_RESET_MIN_POSITIONS:
        tickers = list(db_holdings.keys())
        for ticker in tickers:
            repo.delete_portfolio_holding(broker_id, ticker)
        result["account_reset_detected"] = True
        result["ghost_positions"] = tickers
        result["success"] = True
        _notify(
            f"🚨 [Reconciler][{broker_id}] 모의계좌 리셋 감지! 계좌는 비어있으나 DB에 "
            f"{len(tickers)}개 포지션이 남아있어 전량 정리했습니다 (stopped_positions 쿨다운 기록은 보존됨).\n"
            f"정리된 종목: {', '.join(tickers)}",
            notify,
        )
        return result

    # 시나리오 a) 유령 포지션: DB에는 있으나 계좌에는 없음 → DB 레코드 정리
    for ticker, row in db_holdings.items():
        if ticker not in broker_holdings:
            repo.delete_portfolio_holding(broker_id, ticker)
            result["ghost_positions"].append(ticker)
            _notify(
                f"🗑️ [Reconciler][{broker_id}] 유령 포지션 정리: {row.get('stk_nm', ticker)}({ticker}) "
                f"DB 기록을 삭제했습니다 (계좌에 보유 없음).",
                notify,
            )

    # 시나리오 b) 미기록 포지션 / c) 수량·단가 불일치
    for ticker, bh in broker_holdings.items():
        db_row = db_holdings.get(ticker)

        if db_row is None:
            # 시나리오 b) 계좌에는 있으나 DB에 없음 → 신규 등록 (peak_close = 현재 평균단가로 초기화)
            repo.reconcile_portfolio_holding(
                broker_id=broker_id,
                stk_cd=ticker,
                stk_nm=bh["name"],
                rmnd_qty=bh["quantity"],
                pur_pric=bh["avg_price"],
                cur_prc=bh["current_price"],
                prft_rt=bh["profit_rate"],
            )
            result["unrecorded_positions"].append(ticker)
            _notify(
                f"➕ [Reconciler][{broker_id}] 미기록 포지션 등록: {bh['name']}({ticker}) "
                f"{bh['quantity']}주 @ {bh['avg_price']:,.0f}원 (peak_close는 현재 평균단가로 초기화)",
                notify,
            )
            continue

        qty_mismatch = int(db_row.get("rmnd_qty", 0)) != bh["quantity"]
        price_mismatch = abs(float(db_row.get("pur_pric", 0.0) or 0.0) - bh["avg_price"]) > 0.5

        if qty_mismatch or price_mismatch:
            # 시나리오 c) 수량/단가 불일치 → 브로커 값으로 갱신 (max_profit_rate는 건드리지 않음)
            repo.reconcile_portfolio_holding(
                broker_id=broker_id,
                stk_cd=ticker,
                stk_nm=bh["name"],
                rmnd_qty=bh["quantity"],
                pur_pric=bh["avg_price"],
                cur_prc=bh["current_price"],
                prft_rt=bh["profit_rate"],
            )
            result["mismatched_positions"].append(ticker)
            _notify(
                f"⚠️ [Reconciler][{broker_id}] 포지션 불일치 갱신: {bh['name']}({ticker}) "
                f"DB(수량 {db_row.get('rmnd_qty')}, 단가 {float(db_row.get('pur_pric', 0.0) or 0.0):,.0f}원) → "
                f"계좌(수량 {bh['quantity']}, 단가 {bh['avg_price']:,.0f}원)",
                notify,
            )

    result["success"] = True
    logger.info(
        f"✅ [Reconciler][{broker_id}] 대사 완료 (유령: {len(result['ghost_positions'])}, "
        f"신규등록: {len(result['unrecorded_positions'])}, 불일치갱신: {len(result['mismatched_positions'])})"
    )
    return result
