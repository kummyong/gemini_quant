# kiwoom_rest_bot/data/kiwoom_api_manager.py
import requests
import time
import logging

class KiwoomApiManager:
    """키움증권 API 요청을 관리합니다. (일봉 데이터 수집 기능 포함)"""

    def __init__(self, config):
        self.config = config
        self.access_token = self._get_access_token()

    def _get_access_token(self):
        res_json = self._request_api(
            path="/oauth2/token",
            headers={"Content-Type": "application/json;charset=UTF-8"},
            body={
                "grant_type": "client_credentials",
                "appkey": self.config.kiwoom_app_key,
                "secretkey": self.config.kiwoom_app_secret,
            },
        )
        if res_json and "token" in res_json:
            logging.info("✅ 토큰 발급 성공")
            return res_json["token"]
        logging.error(f"❌ 토큰 발급 실패: {res_json}")
        return None

    def get_kospi_tickers(self):
        if not self.access_token:
            return []
        headers = {
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.config.kiwoom_app_key,
            "appsecret": self.config.kiwoom_app_secret,
            "api-id": "ka10099",
        }
        body = {"mrkt_tp": "0"}
        res_json = self._request_api(
            path="/api/dostk/stkinfo", headers=headers, body=body
        )
        return res_json.get("list", []) if res_json else []

    def get_financial_info(self, ticker: str):
        """API를 통해 시가총액 정보를 가져옵니다."""
        if not self.access_token:
            return None
        headers = {
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.config.kiwoom_app_key,
            "appsecret": self.config.kiwoom_app_secret,
            "api-id": "ka10001",
        }
        body = {"stk_cd": ticker}
        res_json = self._request_api(
            path="/api/dostk/stkinfo", headers=headers, body=body
        )
        if res_json and res_json.get("mac"):
            try:
                return {"mac": float(res_json.get("mac", 0)) * 100000000}
            except (ValueError, TypeError):
                return None
        return None

    def get_daily_chart_data(self, ticker, start_date):
        """지정된 기간의 일봉 데이터를 API로 요청합니다."""
        if not self.access_token:
            return []

        headers = {
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.config.kiwoom_app_key,
            "appsecret": self.config.kiwoom_app_secret,
            "api-id": "ka10081",
        }
        body = {"stk_cd": ticker, "base_dt": start_date, "upd_stkpc_tp": "1"}
        res_json = self._request_api(
            path="/api/dostk/chart", headers=headers, body=body
        )

        # API 응답 오류 코드 확인 로직 추가
        if res_json and res_json.get("return_code") == 0:
            return res_json.get("stk_dt_pole_chart_qry", [])
        else:
            logging.error(f"❌ 일봉 데이터 수신 오류: [{ticker}] {res_json}")
            return []

    def _request_api(self, path, headers=None, body=None, params=None):
        url = f"{self.config.base_url}{path}"
        try:
            if body:
                res = requests.post(url, headers=headers, json=body, timeout=10)
            else:
                res = requests.get(url, headers=headers, params=params, timeout=10)
            time.sleep(1)  # API 과부하 방지를 위해 요청 간격 조절
            res.raise_for_status()
            return res.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"API 요청 실패: {url} - {e}")
            return None

    def get_order_history(self, account_no: str):
        """당일 주문 및 체결 내역을 조회합니다 (ka10018)."""
        if not self.access_token:
            return None

        headers = {
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.config.kiwoom_app_key,
            "appsecret": self.config.kiwoom_app_secret,
            "api-id": "ka10018",
        }
        # 당일 전체 체결 내역 조회 파라미터
        body = {
            "acc_no": account_no,
            "pw": "", # 비밀번호 비워둠
            "qry_tp": "2", # 2: 체결/미체결 전체
            "stk_cd": "" # 공백이면 전체 종목
        }
        res_json = self._request_api(
            path="/api/dostk/order_list", headers=headers, body=body
        )
        return res_json if res_json else {"status": "error", "message": "No response"}
