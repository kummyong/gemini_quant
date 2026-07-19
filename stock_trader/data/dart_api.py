"""
DART OpenAPI 래퍼 클래스
────────────────────────
모든 DART API 호출을 중앙화하고, 고유번호 매핑/캐싱을 관리한다.
"""
import os
import json
import zipfile
import io
import xml.etree.ElementTree as ET
import requests
import configparser
import logging
import time
from stock_trader.config import STOCK_TRADER_DIR

logger = logging.getLogger("DartAPI")

class DartAPI:
    BASE_URL = "https://opendart.fss.or.kr/api"
    
    def __init__(self):
        self.api_key = self._load_api_key()
        self.corp_code_map = {}  # {종목코드6자리: 고유번호8자리}
        self._load_corp_codes()
    
    def _load_api_key(self) -> str:
        """config.ini에서 DART API 키를 로드한다."""
        config = configparser.ConfigParser()
        config_path = os.path.join(STOCK_TRADER_DIR, "broker", "kiwoom", "Kiwoom_MCP_Server", "config.ini")
        if not os.path.exists(config_path):
            config_path = os.path.join(STOCK_TRADER_DIR, "Kiwoom_MCP_Server", "config.ini")
        config.read(config_path)
        
        if "DART" in config and "api_key" in config["DART"]:
            key = config["DART"]["api_key"].strip('"')
            logger.info(f"✅ DART API 키 로드 완료 (길이: {len(key)})")
            return key
        else:
            logger.error("❌ config.ini에서 DART API 키를 찾을 수 없습니다.")
            return ""
    
    def _load_corp_codes(self):
        """종목코드 → DART 고유번호 매핑을 로드한다. 캐시 파일이 없으면 DART에서 다운로드한다."""
        cache_path = os.path.join(os.path.dirname(__file__), "dart_corp_codes.json")
        
        # 캐시 파일이 존재하고 30일 이내면 캐시 사용
        if os.path.exists(cache_path):
            file_age_days = (time.time() - os.path.getmtime(cache_path)) / 86400
            if file_age_days < 30:
                with open(cache_path, "r", encoding="utf-8") as f:
                    self.corp_code_map = json.load(f)
                logger.info(f"✅ DART 고유번호 캐시 로드 완료 ({len(self.corp_code_map)}개 기업)")
                return
        
        # DART에서 전체 고유번호 목록 다운로드
        logger.info("📥 DART 고유번호 전체 목록 다운로드 시작...")
        try:
            url = f"{self.BASE_URL}/corpCode.xml"
            res = requests.get(url, params={"crtfc_key": self.api_key}, timeout=30)
            
            if res.status_code != 200:
                logger.error(f"❌ 고유번호 다운로드 실패: HTTP {res.status_code}")
                return
            
            # ZIP 파일 내부의 XML 파싱
            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                xml_filename = z.namelist()[0]  # 보통 CORPCODE.xml
                with z.open(xml_filename) as xml_file:
                    tree = ET.parse(xml_file)
                    root = tree.getroot()
            
            mapping = {}
            for item in root.findall(".//list"):
                corp_code = item.findtext("corp_code", "").strip()
                stock_code = item.findtext("stock_code", "").strip()
                corp_name = item.findtext("corp_name", "").strip()
                
                # stock_code가 비어있는 기업은 비상장사이므로 제외
                if stock_code and corp_code:
                    mapping[stock_code] = {
                        "corp_code": corp_code,
                        "corp_name": corp_name
                    }
            
            self.corp_code_map = mapping
            
            # 캐시 파일 저장
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(mapping, f, ensure_ascii=False, indent=2)
            
            logger.info(f"✅ DART 고유번호 다운로드 및 캐싱 완료 ({len(mapping)}개 상장사)")
        except Exception as e:
            logger.error(f"❌ DART 고유번호 다운로드 중 오류: {e}")
    
    def get_corp_code(self, stock_code: str) -> str:
        """6자리 종목코드를 8자리 DART 고유번호로 변환한다."""
        entry = self.corp_code_map.get(stock_code)
        if entry:
            return entry["corp_code"]
        logger.warning(f"⚠️ 종목코드 {stock_code}에 대한 DART 고유번호를 찾을 수 없습니다.")
        return ""
    
    def _request(self, endpoint: str, params: dict) -> dict:
        """DART API 공통 요청 메서드. 인증키를 자동 삽입하고 에러를 처리한다."""
        params["crtfc_key"] = self.api_key
        url = f"{self.BASE_URL}/{endpoint}"
        
        try:
            res = requests.get(url, params=params, timeout=15)
            if res.status_code == 200:
                data = res.json()
                status = data.get("status", "")
                if status == "000":  # 정상 응답
                    return data
                elif status == "013":  # 조회된 데이터가 없음
                    logger.info(f"ℹ️ DART 응답: 데이터 없음 (endpoint={endpoint})")
                    return {}
                else:
                    msg = data.get("message", "알 수 없는 오류")
                    logger.warning(f"⚠️ DART API 오류: {status} - {msg} (endpoint={endpoint})")
                    return {}
            else:
                logger.error(f"❌ DART HTTP 오류: {res.status_code} (endpoint={endpoint})")
                return {}
        except Exception as e:
            logger.error(f"❌ DART 요청 실패: {e} (endpoint={endpoint})")
            return {}
    
    # ─── 재무제표 API ───
    
    def get_financial_statements(self, stock_code: str, year: int, report_code: str = "11011", fs_div: str = "CFS") -> list:
        """
        단일 회사의 전체 재무제표를 조회한다.
        
        Args:
            stock_code: 6자리 종목코드 (예: "005930")
            year: 사업연도 (예: 2025)
            report_code: 보고서 코드
                - "11011": 사업보고서 (연간)
                - "11013": 1분기보고서
                - "11012": 반기보고서
                - "11014": 3분기보고서
            fs_div: "CFS"=연결재무제표, "OFS"=개별재무제표
        
        Returns:
            list of dict: 재무제표 항목 리스트. 각 항목은 account_nm(계정명), thstrm_amount(당기금액) 등 포함.
        """
        corp_code = self.get_corp_code(stock_code)
        if not corp_code:
            return []
        
        data = self._request("fnlttSinglAcntAll.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code,
            "fs_div": fs_div
        })
        
        return data.get("list", [])
    
    # ─── 배당 API ───
    
    def get_dividend_info(self, stock_code: str, year: int, report_code: str = "11011") -> list:
        """
        배당에 관한 사항을 조회한다.
        
        Returns:
            list of dict: 배당 정보. 주당 배당금, 배당수익률, 배당성향 등 포함.
        """
        corp_code = self.get_corp_code(stock_code)
        if not corp_code:
            return []
        
        data = self._request("alotMatter.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code
        })
        
        return data.get("list", [])
    
    # ─── 대량보유 API ───
    
    def get_major_shareholders(self, stock_code: str) -> list:
        """
        5% 이상 대량보유 상황보고를 조회한다.
        
        Returns:
            list of dict: 대량보유자 정보. 보유주식수, 보유비율, 변동사유 등 포함.
        """
        corp_code = self.get_corp_code(stock_code)
        if not corp_code:
            return []
        
        data = self._request("majorstock.json", {
            "corp_code": corp_code
        })
        
        return data.get("list", [])
    
    # ─── 임원ㆍ주요주주 특정증권등 소유상황보고서 API ───
    
    def get_executive_shareholders(self, stock_code: str) -> list:
        """
        임원ㆍ주요주주 특정증권등 소유상황보고서를 조회한다.
        
        Returns:
            list of dict: 임원 소유 현황 (증감사유, 변동수량 등 포함)
        """
        corp_code = self.get_corp_code(stock_code)
        if not corp_code:
            return []
        
        data = self._request("elestock.json", {
            "corp_code": corp_code
        })
        
        return data.get("list", [])
    
    # ─── 공시 검색 API ───
    
    def search_disclosures(self, bgn_de: str, end_de: str, corp_code: str = "", corp_cls: str = "Y", page_count: int = 100, page_no: int = 1) -> list:
        """
        공시 검색 (특정 기간의 공시 목록 조회).

        Args:
            bgn_de: 시작일 (YYYYMMDD)
            end_de: 종료일 (YYYYMMDD)
            corp_code: 특정 회사 고유번호 (빈 문자열이면 전체)
            corp_cls: "Y"=유가증권, "K"=코스닥, "N"=코넥스, "E"=기타
            page_count: 한 페이지 결과 수 (최대 100)
            page_no: 페이지 번호 (1부터)

        Returns:
            list of dict: 공시 목록. corp_name, report_nm, rcept_dt, rcept_no 등 포함.
        """
        params = {
            "bgn_de": bgn_de,
            "end_de": end_de,
            "corp_cls": corp_cls,
            "page_count": str(page_count),
            "page_no": str(page_no),
            "sort": "date",
            "sort_mth": "desc"
        }
        if corp_code:
            params["corp_code"] = corp_code

        data = self._request("list.json", params)
        return data.get("list", [])

    def search_disclosures_all(self, bgn_de: str, end_de: str, corp_cls: str = "Y", max_pages: int = 20) -> list:
        """공시 검색을 total_page 기준으로 전 페이지 순회하여 합친다.
        활황장에는 하루 공시가 수백 건이라 1페이지(최대 100건)만 보면 뒷 페이지의
        보고서를 통째로 놓친다. max_pages는 폭주 방어용 상한."""
        params = {
            "bgn_de": bgn_de, "end_de": end_de, "corp_cls": corp_cls,
            "page_count": "100", "page_no": "1", "sort": "date", "sort_mth": "desc"
        }
        data = self._request("list.json", params)
        results = list(data.get("list", []))
        try:
            total_page = int(data.get("total_page", 1))
        except (TypeError, ValueError):
            total_page = 1

        for page in range(2, min(total_page, max_pages) + 1):
            params["page_no"] = str(page)
            page_data = self._request("list.json", params)
            page_list = page_data.get("list", [])
            if not page_list:
                break
            results.extend(page_list)
            time.sleep(0.2)
        return results

    def get_document_text(self, rcept_no: str) -> str:
        """공시 원문(document.xml)을 내려받아 텍스트로 반환한다. 실패 시 빈 문자열.
        elestock.json에는 취득방법(장내매수/스톡옵션 등) 필드가 없어서,
        장내매수 여부는 보고서 원문 텍스트에서만 확인할 수 있다."""
        if not rcept_no:
            return ""
        try:
            res = requests.get(f"{self.BASE_URL}/document.xml",
                               params={"crtfc_key": self.api_key, "rcept_no": rcept_no}, timeout=30)
            if res.status_code != 200:
                logger.warning(f"⚠️ DART 원문 조회 HTTP 오류: {res.status_code} (rcept_no={rcept_no})")
                return ""
            with zipfile.ZipFile(io.BytesIO(res.content)) as z:
                names = z.namelist()
                if not names:
                    return ""
                raw = z.read(names[0])
            for enc in ("utf-8", "cp949"):
                try:
                    return raw.decode(enc)
                except UnicodeDecodeError:
                    continue
            return raw.decode("utf-8", errors="ignore")
        except zipfile.BadZipFile:
            logger.warning(f"⚠️ DART 원문 응답이 ZIP이 아님 (rcept_no={rcept_no}) — 오류 응답 가능성")
            return ""
        except Exception as e:
            logger.warning(f"⚠️ DART 원문 조회 실패 (rcept_no={rcept_no}): {e}")
            return ""
    
    # ─── 최대주주 API ───
    
    def get_largest_shareholder(self, stock_code: str, year: int, report_code: str = "11011") -> list:
        """최대주주 현황을 조회한다."""
        corp_code = self.get_corp_code(stock_code)
        if not corp_code:
            return []
        
        data = self._request("hyslrSttus.json", {
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": report_code
        })
        
        return data.get("list", [])
