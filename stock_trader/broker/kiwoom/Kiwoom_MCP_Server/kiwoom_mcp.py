import requests
import time
import logging
import configparser
import os
from mcp.server.fastmcp import FastMCP
from kiwoom_errors import get_error_message

# 로깅 설정
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


class KiwoomConfig:
    def __init__(self, mode="MOCK"):
        self.mode = mode
        self.base_url = ""
        self.app_key = ""
        self.app_secret = ""
        self.account_no = ""
        self.load_config(mode)

    def load_config(self, mode):
        config = configparser.ConfigParser()
        # 스크립트 파일의 디렉토리를 기준으로 config.ini 경로 설정
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, "config.ini")
        
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found at {config_path}")
            
        config.read(config_path)

        section = f"KIWOOM_{mode.upper()}"
        if section not in config:
            available_sections = [s for s in config.sections() if s.startswith("KIWOOM_")]
            raise ValueError(f"Config section {section} not found. Available: {available_sections}")

        self.mode = mode.upper()
        self.base_url = config[section]["base_url"].strip('"')
        self.app_key = config[section]["app_key"].strip('"')
        self.app_secret = config[section]["app_secret"].strip('"')
        self.account_no = config[section]["account_no"].strip('"')
        logging.info(f"⚙️ 설정 로드 완료: {self.mode} (계좌: {self.account_no})")


class KiwoomApiManager:
    """키움증권 REST API 매니저 (MCP 서버용 확장 버전)"""

    def __init__(self, config: KiwoomConfig):
        self.config = config
        self.access_token = self._get_access_token()

    def switch_mode(self, mode: str):
        """설정 모드 전환 (MOCK, REAL1, REAL2 등)"""
        try:
            self.config.load_config(mode)
            self.access_token = self._get_access_token() # 새 설정으로 토큰 재발급
            return {"status": "success", "message": f"Switched to {mode} mode", "account_no": self.config.account_no}
        except Exception as e:
            logging.error(f"❌ 모드 전환 실패: {e}")
            return {"status": "error", "message": str(e)}

    def _get_access_token(self, max_retries=5):
        url = f"{self.config.base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.config.app_key,
            "secretkey": self.config.app_secret,
        }

        delay = 1  # 시작 대기 시간 (초)
        for i in range(max_retries):
            try:
                res = requests.post(url, json=body, timeout=10)
                if res.status_code == 200:
                    res_json = res.json()
                    if "token" in res_json:
                        logging.info(f"✅ [{self.config.mode}] 키움 API 토큰 발급 성공")
                        return res_json["token"]
                elif res.status_code == 429:
                    logging.warning(
                        f"⚠️ 레이트 리밋(429) 감지! {delay}초 후 다시 시도합니다... ({i+1}/{max_retries})"
                    )
                    time.sleep(delay)
                    delay *= 2
                else:
                    try:
                        res_json = res.json()
                        err_code = res_json.get("return_code") or res_json.get("error_code")
                        err_msg = get_error_message(err_code, res_json.get("message") or res.text)
                        logging.error(
                            f"❌ 토큰 발급 실패 (Status {res.status_code}, Code {err_code}): {err_msg}"
                        )
                    except Exception:
                        logging.error(
                            f"❌ 토큰 발급 실패 (Status {res.status_code}): {res.text}"
                        )
                    break
            except Exception as e:
                logging.error(f"❌ 토큰 발급 중 예외 발생: {e}")
                time.sleep(delay)
                delay *= 2

        return None

    def _request(self, path, api_id, body=None, params=None, max_retries=3):
        url = f"{self.config.base_url}{path}"
        delay = 1

        for i in range(max_retries):
            if not self.access_token:
                self.access_token = self._get_access_token()
                if not self.access_token:
                    return None

            headers = {
                "authorization": f"Bearer {self.access_token}",
                "appkey": self.config.app_key,
                "appsecret": self.config.app_secret,
                "api-id": api_id,
                "Content-Type": "application/json",
            }

            logging.info(f"🚀 [{self.config.mode}] [API 요청] URL: {url}, ID: {api_id}, Body: {body}")

            try:
                if body is not None:
                    res = requests.post(url, headers=headers, json=body, timeout=10)
                else:
                    res = requests.get(url, headers=headers, params=params, timeout=10)

                if res.status_code == 200:
                    response_json = res.json()
                    # logging.info(f"📥 [API 응답] {response_json}")
                    return response_json
                elif res.status_code == 429:
                    logging.warning(
                        f"⚠️ API 레이트 리밋(429) 감지! {delay}초 후 다시 시도합니다... ({i+1}/{max_retries})"
                    )
                    time.sleep(delay)
                    delay *= 2
                    continue
                elif res.status_code == 401:  # 토큰 만료 가능성
                    logging.warning(
                        "⚠️ 토큰 만료(401) 감지, 토큰 재발급 후 다시 시도합니다."
                    )
                    self.access_token = None  # 다음 루프에서 재발급 시도
                    continue
                else:
                    try:
                        res_json = res.json()
                        err_code = res_json.get("return_code") or res_json.get("error_code")
                        err_msg = get_error_message(err_code, res_json.get("message") or res.text)
                        logging.error(
                            f"❌ API 요청 실패 (Status {res.status_code}, Code {err_code}): {err_msg}"
                        )
                        return {"status": "error", "code": err_code, "message": err_msg}
                    except Exception:
                        res.raise_for_status()
            except Exception as e:
                logging.error(f"API 요청 실패 [{api_id}]: {e}")
                if i == max_retries - 1:
                    return {"status": "error", "message": str(e)}
                time.sleep(delay)
                delay *= 2

        return None

    def get_account_list(self):
        """계좌번호 목록 조회 (ka00001)"""
        return self._request("/api/dostk/acnt", "ka00001", body={})

    def get_account_summary(self):
        """계좌 평가 현황 요약 조회 (kt00018)"""
        acc_no = self.config.account_no.replace("-", "")
        if len(acc_no) == 8:
            acc_no += "11"

        body = {
            "acc_no": acc_no,
            "pw": "",
            "qry_tp": "1",  # 1: 합산
            "dmst_stex_tp": "KRX",
        }
        return self._request("/api/dostk/acnt", "kt00018", body=body)

    def get_account_balance(self, qry_tp: str = "2"):
        """계좌 종목별 체결 잔고 조회 (kt00018)
        qry_tp: 1(합산), 2(개별)
        """
        acc_no = self.config.account_no.replace("-", "")
        if len(acc_no) == 8:
            acc_no += "11"

        body = {"acc_no": acc_no, "pw": "", "qry_tp": qry_tp, "dmst_stex_tp": "KRX"}
        return self._request("/api/dostk/acnt", "kt00018", body=body)

    def place_order(
        self,
        stock_code: str,
        quantity: int,
        price: int,
        side: str = "BUY",
        ord_tp: str = "00",
    ):
        """주식 주문 실행 (BUY: kt10000, SELL: kt10001)
        side: BUY(매수), SELL(매도)
        ord_tp: 00(지정가), 03(시장가) 등
        """
        acc_no = self.config.account_no.replace("-", "")
        api_id = "kt10000" if side.upper() == "BUY" else "kt10001"

        body = {
            "acc_no": acc_no,
            "pw": "0000",
            "stk_cd": stock_code,
            "ord_qty": str(quantity),
            "ord_prc": str(price) if ord_tp == "00" else "0",
            "ord_tp": "00",  # 보통(00) 고정
            "unit_tp": "1",  # 1: 단주
            "dmst_stex_tp": "KRX",
            "trde_tp": ord_tp,  # 실제 주문 구분(00, 03)을 여기에 설정
        }
        return self._request("/api/dostk/ordr", api_id, body=body)

    def get_stock_info(self, stock_code: str):
        """주식 현재가 및 기본 정보 조회 (ka10001)"""
        body = {"stk_cd": stock_code}
        return self._request("/api/dostk/stkinfo", "ka10001", body=body)

    def get_stock_list(self, market_type: str = "000", sort_type: str = "1"):
        """시장별 종목 리스트 조회 (Ranking API ka10027 기반)
        market_type: 000(전체), 001(코스피), 101(코스닥)
        sort_type: 1(상승률), 3(하락률)
        """
        # 기존 market_type(0, 10) 호환성 유지
        m_map = {"0": "001", "10": "101"}
        m_type = m_map.get(market_type, market_type)

        return self.get_price_ranking(market_type=m_type, sort_type=sort_type)

    def get_price_ranking(self, market_type: str = "000", sort_type: str = "3"):
        """전일대비 등락률 순위 조회 (ka10027)
        market_type: 000(전체), 001(코스피), 101(코스닥)
        sort_type: 1(상승률), 2(상승폭), 3(하락률), 4(하락폭)
        """
        body = {
            "mrkt_tp": market_type,
            "sort_tp": sort_type,
            "trde_qty_cnd": "0000",  # 전체조회
            "stk_cnd": "1",  # 관리종목제외
            "trde_qty_tp": "0",  # 전체조회
            "crd_cnd": "0",  # 신용조건 (전체조회) - 누락 수정
            "updown_incls": "1",  # 상하한가 포함 - 누락 수정
            "dt": "5",  # 기간 (5일) - 누락 수정
            "pric_cnd": "0",  # 전체조회
            "trde_prica_cnd": "0",  # 전체조회
            "stex_tp": "1",  # KRX
        }
        return self._request("/api/dostk/rkinfo", "ka10027", body=body)

    def get_volume_ranking(self, market_type: str = "000"):
        """전일 거래량 상위 순위 조회 (ka10031)
        market_type: 000(전체), 001(코스피), 101(코스닥)
        """
        body = {
            "mrkt_tp": market_type,
            "stk_cnd": "1",  # 관리종목제외
            "crd_cnd": "0",  # 전체조회
            "stex_tp": "1",  # KRX
        }
        return self._request("/api/dostk/rkinfo", "ka10031", body=body)

    def get_value_ranking(self, market_type: str = "000"):
        """거래대금 상위 순위 조회 (ka10032)
        market_type: 000(전체), 001(코스피), 101(코스닥)
        """
        body = {
            "mrkt_tp": market_type,
            "stk_cnd": "1",  # 관리종목제외
            "stex_tp": "1",  # KRX
        }
        return self._request("/api/dostk/rkinfo", "ka10032", body=body)

    def get_order_history(self):
        """당일 체결 및 주문 내역 조회"""
        return self.api.get_order_history(self.config.account_no)

    def get_daily_chart(self, stock_code: str, base_date: str):
        """주식 일봉 차트 조회 (ka10081)
        base_date: YYYYMMDD
        """
        body = {
            "stk_cd": stock_code,
            "base_dt": base_date,
            "upd_stkpc_tp": "1",  # 1: 수정주가
        }
        return self._request("/api/dostk/chart", "ka10081", body=body)

    def set_account_no(self, account_no: str):
        """계좌 번호를 변경합니다."""
        self.config.account_no = account_no
        logging.info(f"✅ 계좌 번호가 {account_no}로 변경되었습니다.")
        return {"status": "success", "message": f"Account changed to {account_no}"}


# MCP 서버 설정
mcp = FastMCP("KiwoomManager")
kiwoom_config = KiwoomConfig(mode="MOCK")
kiwoom_api = KiwoomApiManager(kiwoom_config)


@mcp.tool()
def switch_mode(mode: str) -> dict:
    """키움증권 API 모드를 전환합니다. (예: MOCK, REAL1, REAL2)"""
    return kiwoom_api.switch_mode(mode)


@mcp.tool()
def get_account_list() -> dict:
    """사용 가능한 키움증권 계좌번호 목록을 조회합니다."""
    return kiwoom_api.get_account_list()


@mcp.tool()
def get_account_summary() -> dict:
    """계좌의 예수금, 총 평가금액, 손익 등 요약 정보를 조회합니다."""
    return kiwoom_api.get_account_summary()


@mcp.tool()
def get_balance(qry_tp: str = "2") -> dict:
    """현재 설정된 키움증권 계좌의 잔고 및 보유 종목 현황을 조회합니다. qry_tp: 1(합산), 2(개별)"""
    return kiwoom_api.get_account_balance(qry_tp)


@mcp.tool()
def get_stock_price(stock_code: str) -> dict:
    """특정 종목의 현재가 및 기본 정보를 조회합니다."""
    return kiwoom_api.get_stock_info(stock_code)


@mcp.tool()
def get_stock_list(market_type: str = "000", sort_type: str = "1") -> dict:
    """시장별 등락률 순위 기반 종목 리스트를 조회합니다. market_type: 000(전체), 0(코스피), 10(코스닥) / sort_type: 1(상승률), 3(하락률)"""
    return kiwoom_api.get_stock_list(market_type, sort_type)


@mcp.tool()
def get_volume_ranking(market_type: str = "000") -> dict:
    """전일 거래량 상위 순위를 조회합니다. market_type: 000(전체), 001(코스피), 101(코스닥)"""
    return kiwoom_api.get_volume_ranking(market_type)


@mcp.tool()
def get_value_ranking(market_type: str = "000") -> dict:
    """거래대금 상위 순위를 조회합니다. market_type: 000(전체), 001(코스피), 101(코스닥)"""
    return kiwoom_api.get_value_ranking(market_type)


@mcp.tool()
def get_daily_chart(stock_code: str, base_date: str) -> dict:
    """특정 종목의 일봉 차트 데이터를 조회합니다. base_date: YYYYMMDD 형식"""
    return kiwoom_api.get_daily_chart(stock_code, base_date)


@mcp.tool()
def get_price_ranking(market_type: str = "000", sort_type: str = "3") -> dict:
    """전일대비 등락률 순위를 조회합니다. market_type: 000(전체), 001(코스피), 101(코스닥) / sort_type: 1(상승률), 3(하락률)"""
    return kiwoom_api.get_price_ranking(market_type, sort_type)


@mcp.tool()
def get_top_decliners(market_type: str = "000") -> dict:
    """전일대비 하락률이 가장 높은 상위 종목들을 조회합니다."""
    return kiwoom_api.get_price_ranking(market_type, sort_type="3")


@mcp.tool()
def change_account(account_no: str) -> dict:
    """현재 연결된 키움증권 계좌를 변경합니다. (형식: 8122-3713-11 또는 8122371311)"""
    return kiwoom_api.set_account_no(account_no)


@mcp.tool()
def place_order(
    stock_code: str, quantity: int, price: int, side: str = "BUY", ord_tp: str = "00"
) -> dict:
    """주식을 매수(BUY)하거나 매도(SELL)합니다. ord_tp: 00(지정가), 03(시장가)"""
    return kiwoom_api.place_order(stock_code, quantity, price, side, ord_tp)


if __name__ == "__main__":
    mcp.run()
