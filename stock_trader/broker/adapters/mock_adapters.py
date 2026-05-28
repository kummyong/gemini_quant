# mock_adapters.py
"""Mock and real adapters for broker integrations.
The `KoreaInvestmentAdapter` now supports real KIS REST API calls when credentials are provided.
If the API cannot be initialised, it gracefully falls back to the original in‑memory mock behaviour.
"""

from stock_trader.broker.broker_interface import BrokerInterface
from typing import Dict, List
import logging
from stock_trader.broker.api.korea_investment_api import KoreaInvestmentAPI


class KoreaInvestmentAdapter(BrokerInterface):
    """Adapter for Korea Investment Securities.
    It operates in two modes:
    * **Real mode** – uses `KoreaInvestmentAPI` to call the live KIS REST API.
    * **Mock mode** – falls back to the original in‑memory simulation when credentials are missing or init fails.
    The `use_mock` flag can force mock mode (useful for testing).
    """

    def __init__(self, initial_balance: float = 10000000.0, use_mock: bool = False):
        self.logger = logging.getLogger(__name__)
        self.balance = initial_balance
        self.holdings: List[Dict] = [
            {
                "stk_cd": "A005930",
                "stk_nm": "삼성전자",
                "rmnd_qty": 10,
                "pchs_amt": 700000,
                "evlt_amt": 720000,
                "prft_rt": "2.85",
            }
        ]
        self.use_mock = use_mock
        if not self.use_mock:
            try:
                self.api = KoreaInvestmentAPI()
            except Exception as e:
                self.logger.warning(f"KIS API init failed ({e}); falling back to mock mode.")
                self.use_mock = True

    # ---------------------------------------------------------------------
    # Helper for mock calculations (used when API not available)
    # ---------------------------------------------------------------------
    def _mock_account_summary(self) -> Dict:
        tot_pur = sum(h["pchs_amt"] for h in self.holdings)
        tot_evlt = sum(h["evlt_amt"] for h in self.holdings)
        prft_rt = f"{((tot_evlt - tot_pur) / max(1, tot_pur)) * 100:.2f}"
        return {
            "tot_pur_amt": str(tot_pur),
            "tot_evlt_amt": str(tot_evlt),
            "tot_prft_rt": prft_rt,
            "prsm_dpst_aset_amt": str(int(self.balance)),
            "acnt_evlt_remn_indv_tot": self.holdings,
        }

    # ---------------------------------------------------------------------
    # BrokerInterface implementations
    # ---------------------------------------------------------------------
    def get_account_summary(self) -> Dict:
        if self.use_mock:
            return self._mock_account_summary()
        return self.api.get_account_summary()

    def place_order(self, stock_code: str, quantity: int, price: float, side: str) -> Dict:
        if self.use_mock:
            cost = price * quantity
            if side.upper() == "BUY":
                if self.balance >= cost:
                    self.balance -= cost
                    found = False
                    for h in self.holdings:
                        if h["stk_cd"] == f"A{stock_code}":
                            h["rmnd_qty"] += quantity
                            h["pchs_amt"] += cost
                            h["evlt_amt"] += cost
                            found = True
                            break
                    if not found:
                        self.holdings.append({
                            "stk_cd": f"A{stock_code}",
                            "stk_nm": f"한투종목_{stock_code}",
                            "rmnd_qty": quantity,
                            "pchs_amt": cost,
                            "evlt_amt": cost,
                            "prft_rt": "0.00",
                        })
                    return {"return_code": 0, "msg": "한투 매수 체결 완료"}
                else:
                    return {"return_code": 1, "msg": "잔고 부족"}
            else:  # SELL
                for h in self.holdings:
                    if h["stk_cd"] == f"A{stock_code}":
                        if h["rmnd_qty"] >= quantity:
                            h["rmnd_qty"] -= quantity
                            self.balance += cost
                            if h["rmnd_qty"] == 0:
                                self.holdings.remove(h)
                            return {"return_code": 0, "msg": "한투 매도 체결 완료"}
                return {"return_code": 1, "msg": "보유 수량 부족"}
        else:
            return self.api.place_order(stock_code, quantity, price, side)

    def cancel_order(self, order_no: str) -> bool:
        if self.use_mock:
            return True
        try:
            self.api._request("POST", f"/v1/trading/order/{order_no}/cancel")
            return True
        except Exception as e:
            self.logger.error(f"Failed to cancel KIS order {order_no}: {e}")
            return False

    def get_current_price(self, stock_code: str) -> float:
        if self.use_mock:
            return 72000.0
        return self.api.get_current_price(stock_code)


class NhInvestmentAdapter(BrokerInterface):
    """NH투자증권 연동을 시뮬레이션하는 Mock 어댑터입니다."""

    def __init__(self, initial_balance: float = 20000000.0):
        self.balance = initial_balance
        self.holdings: List[Dict] = [
            {
                "stk_cd": "A000660",
                "stk_nm": "SK하이닉스",
                "rmnd_qty": 5,
                "pchs_amt": 800000,
                "evlt_amt": 850000,
                "prft_rt": "6.25",
            }
        ]

    def get_account_summary(self) -> Dict:
        tot_pur = sum(h["pchs_amt"] for h in self.holdings)
        tot_evlt = sum(h["evlt_amt"] for h in self.holdings)
        prft_rt = f"{((tot_evlt - tot_pur) / max(1, tot_pur)) * 100:.2f}"
        return {
            "tot_pur_amt": str(tot_pur),
            "tot_evlt_amt": str(tot_evlt),
            "tot_prft_rt": prft_rt,
            "prsm_dpst_aset_amt": str(int(self.balance)),
            "acnt_evlt_remn_indv_tot": self.holdings,
        }

    def place_order(self, stock_code: str, quantity: int, price: float, side: str) -> Dict:
        cost = price * quantity
        if side.upper() == "BUY":
            if self.balance >= cost:
                self.balance -= cost
                found = False
                for h in self.holdings:
                    if h["stk_cd"] == f"A{stock_code}":
                        h["rmnd_qty"] += quantity
                        h["pchs_amt"] += cost
                        h["evlt_amt"] += cost
                        found = True
                        break
                if not found:
                    self.holdings.append({
                        "stk_cd": f"A{stock_code}",
                        "stk_nm": f"NH종목_{stock_code}",
                        "rmnd_qty": quantity,
                        "pchs_amt": cost,
                        "evlt_amt": cost,
                        "prft_rt": "0.00",
                    })
                return {"return_code": 0, "msg": "NH 매수 체결 완료"}
            else:
                return {"return_code": 1, "msg": "잔고 부족"}
        else:
            for h in self.holdings:
                if h["stk_cd"] == f"A{stock_code}":
                    if h["rmnd_qty"] >= quantity:
                        h["rmnd_qty"] -= quantity
                        self.balance += cost
                        if h["rmnd_qty"] == 0:
                            self.holdings.remove(h)
                        return {"return_code": 0, "msg": "NH 매도 체결 완료"}
            return {"return_code": 1, "msg": "보유 수량 부족"}

    def cancel_order(self, order_no: str) -> bool:
        return True

    def get_current_price(self, stock_code: str) -> float:
        return 170000.0
