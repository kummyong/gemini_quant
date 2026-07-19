"""네이버 금융 크롤링 데이터 프로바이더.

strategy_engine.fetch_market_data()에 인라인으로 있던 네이버 금융 파싱 로직을
분리한 모듈. 종목 메인 페이지(EPS 성장률/업종명/현재가)와 투자자별 매매동향
페이지(기관/외국인 순매수)를 크롤링한다.

파싱 실패 시 예외를 전파하지 않고 로깅 후 기본값을 유지한다(기존 동작 유지).
"""
import logging
import re
from typing import Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry
from bs4 import BeautifulSoup

logger = logging.getLogger("NaverFinance")


def build_session() -> requests.Session:
    """재시도(HTTP 5xx) 어댑터가 장착된 requests 세션 생성"""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    })
    retries = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[500, 502, 503, 504],
        raise_on_status=False,
        allowed_methods=["GET", "POST"]
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    return session


class NaverFinanceProvider:
    """네이버 금융 종목 메인/수급 페이지 크롤러"""

    def __init__(self, session: Optional[requests.Session] = None):
        self.session = session or build_session()

    def fetch_main_info(self, ticker: str, name: str = "") -> Dict:
        """종목 메인 페이지에서 업종명, 연간 EPS 성장률(%), 현재가를 파싱한다.

        반환: {"eps_growth": float, "industry_name": str, "current_price": float}
        현재가 파싱 실패 시 current_price는 0.0으로 반환된다(호출부에서 폴백 처리).
        """
        eps_growth = 0.0
        industry_name = "기타"
        current_price = 0.0
        try:
            main_url = f"https://finance.naver.com/item/main.naver?code={ticker}"
            res = self.session.get(main_url, verify=False, timeout=10)
            res.encoding = 'utf-8'
            soup = BeautifulSoup(res.text, 'lxml')

            match = re.search(r'업종명\s*:\s*<a[^>]*>([^<]+)</a>', res.text)
            if match:
                industry_name = match.group(1).strip()

            div = soup.find('div', class_='section cop_analysis')
            if div:
                table = div.find('table')
                if table:
                    tr_years = table.find_all('tr')[1]
                    years = [th.get_text(strip=True) for th in tr_years.find_all('th')]
                    eps_row = None
                    for tr in table.find_all('tr'):
                        th_text = tr.find('th')
                        if th_text and 'EPS(원)' in th_text.get_text(strip=True):
                            eps_row = [td.get_text(strip=True).replace(',', '') for td in tr.find_all('td')]
                            break
                    if eps_row and len(years) >= 2 and len(eps_row) >= 2:
                        annual_eps = []
                        for y, eps in zip(years, eps_row):
                            if re.match(r'\d{4}\.\d{2}', y) and '(E)' not in y:
                                try:
                                    annual_eps.append(float(eps) if eps and eps != '-' else 0.0)
                                except ValueError:
                                    pass
                        if len(annual_eps) >= 2:
                            latest_eps = annual_eps[-1]
                            prev_eps = annual_eps[-2]
                            if prev_eps != 0:
                                eps_growth = ((latest_eps - prev_eps) / abs(prev_eps)) * 100

            now_val_div = soup.find('p', class_='no_today')
            if now_val_div:
                blind_span = now_val_div.find('span', class_='blind')
                if blind_span:
                    current_price = float(blind_span.get_text(strip=True).replace(',', ''))
        except Exception as e:
            logger.error(f"[{name}] 메인 정보 파싱 실패: {e}")

        return {
            "eps_growth": eps_growth,
            "industry_name": industry_name,
            "current_price": current_price
        }

    def fetch_investor_net_buying(self, ticker: str, name: str = "", current_price: float = 0.0) -> Tuple[float, float, int]:
        """투자자별 매매동향 페이지에서 최근 5거래일 기관+외국인 순매수 대금(억 원) 및 연속 순매수 일수를 파싱한다.

        current_price가 0.0이면 첫 거래일 종가로 대체한다(기존 동작 유지).
        반환: (net_buying_억원, current_price, consecutive_buy_days)
        """
        net_buying = 0.0
        consecutive_buy_days = 0
        is_consecutive_broken = False
        try:
            frgn_url = f"https://finance.naver.com/item/frgn.naver?code={ticker}"
            res = self.session.get(frgn_url, verify=False, timeout=10)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'lxml')
            tables = soup.find_all('table', class_='type2')
            target_table = None
            for t in tables:
                if '기관' in t.get_text() and '외국인' in t.get_text():
                    target_table = t
                    break
            if target_table:
                total_net_buying_krw = 0.0
                day_count = 0
                for tr in target_table.find_all('tr'):
                    tds = [td.get_text(strip=True).replace(',', '') for td in tr.find_all('td')]
                    if len(tds) >= 9 and re.match(r'\d{4}\.\d{2}\.\d{2}', tds[0]):
                        try:
                            price_val = float(tds[1])
                            if current_price == 0.0 and day_count == 0:
                                current_price = price_val
                            inst_vol = float(tds[5]) if tds[5] and tds[5] != '-' else 0.0
                            foreign_vol = float(tds[6]) if tds[6] and tds[6] != '-' else 0.0
                            
                            daily_net_vol = inst_vol + foreign_vol
                            total_net_buying_krw += daily_net_vol * price_val
                            
                            if not is_consecutive_broken:
                                if daily_net_vol > 0:
                                    consecutive_buy_days += 1
                                else:
                                    is_consecutive_broken = True
                                    
                            day_count += 1
                            if day_count >= 5:
                                break
                        except ValueError:
                            pass
                net_buying = total_net_buying_krw / 100000000.0
        except Exception as e:
            logger.error(f"[{name}] 수급 정보 파싱 실패: {e}")

        return net_buying, current_price, consecutive_buy_days

    def fetch_discussion_traffic(self, ticker: str) -> int:
        """종목토론방 1페이지(최상단) 내 당일 작성된 게시글 수를 크롤링한다.
        군중 센티먼트(관심도)의 대용 지표로 사용된다.
        
        반환: int (당일 작성된 게시글 수)
        """
        import datetime
        post_count = 0
        try:
            board_url = f"https://finance.naver.com/item/board.naver?code={ticker}"
            res = self.session.get(board_url, verify=False, timeout=10)
            res.encoding = 'euc-kr'
            soup = BeautifulSoup(res.text, 'lxml')
            
            board_table = soup.find('table', class_='type2')
            if board_table:
                today_str = datetime.datetime.now().strftime("%Y.%m.%d")
                for tr in board_table.find_all('tr'):
                    tds = tr.find_all('td')
                    if len(tds) >= 6:
                        date_text = tds[0].get_text(strip=True)
                        # 네이버 종목토론실은 'YYYY.MM.DD HH:MM' 형식을 띠므로 당일 날짜 포함 여부 체크
                        if today_str in date_text: 
                            post_count += 1
        except Exception as e:
            logger.error(f"[{ticker}] 종목토론방 트래픽 크롤링 실패: {e}")
            
        return post_count

