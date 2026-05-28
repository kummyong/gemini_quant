# korea_investment_api.py
"""Thin wrapper for Korea Investment Securities (KIS) REST API.
This module provides a simple client class that handles authentication,
request building, and basic broker operations such as account summary,
order placement, and price lookup.

The implementation uses ``requests`` and expects configuration via
environment variables or a ``kis_config`` dictionary in ``config.py``.
If the required credentials are missing, the client raises ``RuntimeError``
so that callers can fall back to the mock adapter.
"""

import os
import time
import logging
from typing import Dict, Any

import requests

logger = logging.getLogger(__name__)


class KoreaInvestmentAPI:
    """KIS REST API client.

    The client authenticates using client‑credentials flow and stores an
    access token for subsequent calls. All public methods return plain Python
    objects (dict, float, etc.) and raise ``RuntimeError`` on configuration
    errors.
    """

    def __init__(self, config: Dict[str, Any] = None):
        # Load configuration – precedence: explicit dict > env vars > defaults
        self.config = config or {}
        self.base_url = self.config.get("BASE_URL") or os.getenv("KIS_BASE_URL")
        self.client_id = self.config.get("CLIENT_ID") or os.getenv("KIS_CLIENT_ID")
        self.client_secret = self.config.get("CLIENT_SECRET") or os.getenv("KIS_CLIENT_SECRET")
        self.sandbox = self.config.get("SANDBOX", os.getenv("KIS_SANDBOX", "True")).lower() == "true"

        if not all([self.base_url, self.client_id, self.client_secret]):
            raise RuntimeError(
                "KIS API configuration missing – ensure BASE_URL, CLIENT_ID and CLIENT_SECRET are set."
            )

        self.token: str | None = None
        self.token_expires_at: float = 0
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    # ---------------------------------------------------------------------
    # Authentication
    # ---------------------------------------------------------------------
    def _authenticate(self) -> None:
        """Obtain an OAuth2 access token using client‑credentials grant.

        The exact endpoint and payload are based on public KIS documentation.
        Adjust ``auth_url`` and payload fields if the provider changes.
        """
        auth_url = f"{self.base_url}/oauth2/tokenP"
        payload = {
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": "oob",
        }
        logger.debug("Requesting KIS access token at %s", auth_url)
        resp = self.session.post(auth_url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        self.token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        self.token_expires_at = time.time() + expires_in - 60  # refresh a minute early
        logger.info("Obtained KIS access token, expires in %s seconds", expires_in)

    def _ensure_token(self) -> None:
        if not self.token or time.time() >= self.token_expires_at:
            self._authenticate()

    # ---------------------------------------------------------------------
    # Helper request method
    # ---------------------------------------------------------------------
    def _request(self, method: str, path: str, params: Dict[str, Any] = None, json_body: Dict[str, Any] = None) -> Any:
        self._ensure_token()
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}
        logger.debug("KIS %s %s params=%s json=%s", method, url, params, json_body)
        response = self.session.request(method, url, headers=headers, params=params, json=json_body, timeout=15)
        response.raise_for_status()
        return response.json()

    # ---------------------------------------------------------------------
    # Public broker methods
    # ---------------------------------------------------------------------
    def get_account_summary(self) -> Dict[str, Any]:
        """Return a dictionary compatible with the mock adapter output.
        Example endpoint: ``/v1/account/overview`` (may differ per KIS version).
        """
        data = self._request("GET", "/v1/account/overview")
        total_purchase = data.get("totalPurchaseAmount", 0)
        total_evaluation = data.get("totalEvaluationAmount", 0)
        profit_rate = ((total_evaluation - total_purchase) / max(1, total_purchase)) * 100
        cash = data.get("cashAmount", 0)
        holdings = []
        for item in data.get("holdings", []):
            holdings.append({
                "stk_cd": f"A{item.get('stockCode')}",
                "stk_nm": item.get("stockName"),
                "rmnd_qty": item.get("quantity"),
                "pchs_amt": item.get("purchaseAmount"),
                "evlt_amt": item.get("evaluationAmount"),
                "prft_rt": f"{item.get('profitRate', 0):.2f}",
            })
        return {
            "tot_pur_amt": str(total_purchase),
            "tot_evlt_amt": str(total_evaluation),
            "tot_prft_rt": f"{profit_rate:.2f}",
            "prsm_dpst_aset_amt": str(int(cash)),
            "acnt_evlt_remn_indv_tot": holdings,
        }

    def place_order(self, stock_code: str, quantity: int, price: float, side: str) -> Dict[str, Any]:
        """Place a BUY or SELL order.
        This uses the ``/v1/trading/order`` endpoint (subject to change).
        """
        order_type = "01" if side.upper() == "BUY" else "02"  # 01=Buy, 02=Sell (KIS convention)
        body = {
            "stockCode": stock_code,
            "orderQty": quantity,
            "orderPrice": price,
            "orderType": order_type,
            "accountNo": os.getenv("KIS_ACCOUNT_NO", ""),
        }
        resp = self._request("POST", "/v1/trading/order", json_body=body)
        if resp.get("rt_cd") == "0":
            return {"return_code": 0, "msg": "KIS order placed successfully"}
        else:
            return {"return_code": 1, "msg": resp.get("msg_cd", "Order failed")}

    def get_current_price(self, stock_code: str) -> float:
        """Fetch the latest price for a given stock code.
        Endpoint example: ``/v1/market/price/{stock_code}``.
        """
        data = self._request("GET", f"/v1/market/price/{stock_code}")
        price = data.get("price")
        return float(price) if price is not None else 0.0

# End of file
