# kiwoom_api_core.py
import requests
import time
import logging
import configparser
import os

logger = logging.getLogger("KiwoomCore")

class KiwoomApiCore:
    def __init__(self, mode="MOCK"):
        self.mode = mode.upper()
        self.base_url = ""
        self.app_key = ""
        self.app_secret = ""
        self.account_no = ""
        self.access_token = None
        self._load_config()
        self.access_token = self._get_access_token()

    def _load_config(self):
        config = configparser.ConfigParser()
        config_path = os.path.join(os.path.dirname(__file__), "Kiwoom_MCP_Server", "config.ini")
        if not os.path.exists(config_path):
            config_path = "/root/workspace/gemini-quant/stock_trader/Kiwoom_MCP_Server/config.ini"
            
        config.read(config_path)
        section = f"KIWOOM_{self.mode}"
        if section in config:
            self.base_url = config[section]["base_url"].strip('"')
            self.app_key = config[section]["app_key"].strip('"')
            self.app_secret = config[section]["app_secret"].strip('"')
            self.account_no = config[section]["account_no"].strip('"')
            logger.info(f"⚙️ Core 설정 로드 완료: {self.mode}")
        else:
            logger.error(f"❌ 설정 섹션을 찾을 수 없음: {section}")

    def _get_access_token(self, max_retries=3):
        url = f"{self.base_url}/oauth2/token"
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret,
        }
        
        for i in range(max_retries):
            try:
                res = requests.post(url, json=body, timeout=10)
                if res.status_code == 200:
                    token = res.json().get("token")
                    if token:
                        logger.info("✅ API 토큰 발급 성공")
                        return token
                elif res.status_code == 429:
                    wait_time = (i + 1) * 2
                    logger.warning(f"⚠️ 토큰 발급 레이트 리밋 감지. {wait_time}초 후 재시도 ({i+1}/{max_retries})")
                    time.sleep(wait_time)
                else:
                    logger.error(f"❌ 토큰 발급 실패: {res.status_code} - {res.text}")
            except Exception as e:
                logger.error(f"❌ 토큰 발급 예외: {e}")
                time.sleep(2)
        return None

    def _request(self, path, api_id, body=None, params=None, max_retries=5):
        """레이트 리밋(429) 대응 강화된 요청 메서드"""
        if not self.access_token:
            self.access_token = self._get_access_token()
            if not self.access_token: return None

        url = f"{self.base_url}{path}"
        headers = {
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "api-id": api_id,
            "Content-Type": "application/json",
        }

        for i in range(max_retries):
            try:
                if body is not None:
                    res = requests.post(url, headers=headers, json=body, timeout=15)
                else:
                    res = requests.get(url, headers=headers, params=params, timeout=15)
                
                if res.status_code == 200:
                    data = res.json()
                    # return_code 3: 토큰 만료/유효하지 않음 (일부 API에서 200 OK로 반환됨)
                    if data and data.get("return_code") == 3:
                        logger.warning("⚠️ 토큰 유효성 오류(Code 3) 감지. 재발급 시도.")
                        self.access_token = self._get_access_token()
                        headers["authorization"] = f"Bearer {self.access_token}"
                        continue
                    return data
                elif res.status_code == 429:
                    # 레이트 리밋 발생 시 지수적으로 대기 시간 증가 (2, 4, 8, 16, 32초)
                    wait_time = 2 ** (i + 1)
                    logger.warning(f"⚠️ API 레이트 리밋(429) 감지! {wait_time}초 후 재시도... ({i+1}/{max_retries})")
                    time.sleep(wait_time)
                    continue
                elif res.status_code == 401:
                    logger.warning("⚠️ 토큰 만료 감지. 재발급 시도.")
                    self.access_token = self._get_access_token()
                    headers["authorization"] = f"Bearer {self.access_token}"
                    continue
                else:
                    logger.error(f"❌ API 오류: {res.status_code} - {res.text}")
                    return None
            except Exception as e:
                logger.error(f"❌ 통신 오류: {e}")
                time.sleep(2)
        
        return None

    def get_account_summary(self):
        acc_no = self.account_no.replace("-", "")
        if len(acc_no) == 8: acc_no += "11"
        body = {"acc_no": acc_no, "pw": "", "qry_tp": "1", "dmst_stex_tp": "KRX"}
        return self._request("/api/dostk/acnt", "kt00018", body=body)

    def place_order(self, stock_code, quantity, price, side="BUY"):
        acc_no = self.account_no.replace("-", "")
        api_id = "kt10000" if side.upper() == "BUY" else "kt10001"
        body = {
            "acc_no": acc_no, "pw": "0000", "stk_cd": stock_code,
            "ord_qty": str(quantity), "ord_prc": "0", "ord_tp": "03", 
            "unit_tp": "1", "dmst_stex_tp": "KRX", "trde_tp": "03"
        }
        return self._request("/api/dostk/ordr", api_id, body=body)
