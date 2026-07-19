"""종목별 기관/외국인 수급 조회 — 키움 ka10059 우선, 네이버 크롤링 폴백.

네이버 금융 크롤링(비공식 HTML 파싱)은 구조 변경에 취약해 크리티컬 패스에서
공식 API인 키움 ka10059(종목별투자자기관별요청)로 단계적으로 대체한다.

중요: 스코어러의 net_buying은 종목 간 min-max 정규화에 들어가므로, 한 번의
스코어링 실행 안에서 소스(단위 체계)가 섞이면 정규화가 왜곡된다. 따라서
- 첫 조회 시 키움 가용성(토큰 발급 여부)을 프로브해 그 실행의 소스를 고정하고,
- 키움 소스로 고정된 뒤 개별 종목 조회가 실패하면 소스를 바꾸지 않고
  중립값(순매수 0, 연속일수 0)을 반환한다.
"""
import logging
from typing import Tuple

logger = logging.getLogger("InvestorFlowProvider")

# ka10059 금액 응답의 단위 가정(천원). 문서상 unit_tp="1000" 요청 기준이며,
# 실계좌 키 활성화 후 첫 실행에서 네이버 값과 자릿수를 교차 확인할 것.
_KIWOOM_AMT_UNIT_KRW = 1_000.0
_KRW_PER_EOK = 100_000_000.0


def _to_float(val) -> float:
    try:
        return float(str(val).replace(",", "").replace("+", "").strip() or 0)
    except (TypeError, ValueError):
        return 0.0


class InvestorFlowProvider:
    def __init__(self, naver_provider, kiwoom_api=None):
        """naver_provider: NaverFinanceProvider (필수, 폴백)
        kiwoom_api: KiwoomApiCore 호환 객체 (None이면 첫 조회 시 BrokerFactory에서 획득 시도)"""
        self.naver = naver_provider
        self._kiwoom_api = kiwoom_api
        self._source = None  # None=미결정, "KIWOOM" | "NAVER"

    def _resolve_source(self) -> str:
        """실행 단위 소스 결정. 키움 토큰이 살아있으면 KIWOOM, 아니면 NAVER."""
        if self._source:
            return self._source
        try:
            if self._kiwoom_api is None:
                from stock_trader.broker.broker_factory import BrokerFactory
                self._kiwoom_api = BrokerFactory.get_broker("KIWOOM").api
            if getattr(self._kiwoom_api, "access_token", None):
                self._source = "KIWOOM"
            else:
                self._source = "NAVER"
        except Exception as e:
            logger.warning(f"⚠️ 키움 API 프로브 실패 — 네이버 크롤링으로 폴백: {e}")
            self._source = "NAVER"
        logger.info(f"📡 종목별 수급 소스 확정: {self._source} (실행 단위로 고정)")
        return self._source

    def fetch(self, ticker: str, name: str = "", current_price: float = 0.0) -> Tuple[float, float, int]:
        """최근 5거래일 기관+외국인 순매수 대금(억원 근사)과 연속 순매수 일수를 반환한다.
        반환: (net_buying_억원, current_price, consecutive_buy_days) — 네이버 경로와 동일 시그니처."""
        if self._resolve_source() == "KIWOOM":
            return self._fetch_kiwoom(ticker, name, current_price)
        return self.naver.fetch_investor_net_buying(ticker, name, current_price)

    def _fetch_kiwoom(self, ticker: str, name: str, current_price: float) -> Tuple[float, float, int]:
        try:
            resp = self._kiwoom_api.get_stock_investor_netbuy(ticker)
            rows = (resp or {}).get("stk_invsr_orgn", [])
            if not rows:
                logger.warning(f"⚠️ [{name}] ka10059 응답 비어있음 — 중립값(0) 반환 (소스 혼합 방지)")
                return 0.0, current_price, 0

            # 응답은 일자 내림차순(최근 우선) — 최근 5거래일만 사용
            total_net_krw = 0.0
            consecutive_buy_days = 0
            is_consecutive_broken = False
            for row in rows[:5]:
                daily_net = (_to_float(row.get("frgnr_invsr")) + _to_float(row.get("orgn"))) * _KIWOOM_AMT_UNIT_KRW
                total_net_krw += daily_net
                if not is_consecutive_broken:
                    if daily_net > 0:
                        consecutive_buy_days += 1
                    else:
                        is_consecutive_broken = True

            if current_price <= 0:
                current_price = abs(_to_float(rows[0].get("cur_prc")))

            return total_net_krw / _KRW_PER_EOK, current_price, consecutive_buy_days
        except Exception as e:
            logger.warning(f"⚠️ [{name}] ka10059 조회 실패 — 중립값(0) 반환 (소스 혼합 방지): {e}")
            return 0.0, current_price, 0
