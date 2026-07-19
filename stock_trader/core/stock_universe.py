"""
gemini_quant 통합 종목 매핑 사전
──────────────────────────────
종목명 → 종목코드 매핑을 한 곳에서 관리합니다.
local_intent_router.py, strategy_engine.py 등에서 import하여 사용합니다.
"""

import os
import json

# 종목명(한글/영문) → 6자리 종목코드 매핑
STOCK_MAP = {
    # 대형주
    "삼성전자": "005930", "삼성": "005930",
    "SK하이닉스": "000660", "하이닉스": "000660",
    "현대차": "005380", "현대자동차": "005380",
    "기아": "000270",
    "셀트리온": "068270",
    "POSCO홀딩스": "005490", "포스코홀딩스": "005490",
    "KB금융": "105560",
    "신한지주": "055550",
    "NAVER": "035420", "네이버": "035420",
    "카카오": "035720",
    "LG화학": "051910",
    "삼성화재": "000810",
    "삼성생명": "032830",
    "한국전력": "015760",
    "KT&G": "033780",
    "LG": "003550",
    "유한양행": "000100",
    # 바이오/2차전지
    "LG에너지솔루션": "373220", "엔솔": "373220",
    "삼성바이오로직스": "207940", "삼바": "207940",
    "에코프로": "086520", "에코프로비엠": "247540",
    # 반도체/IT
    "한미반도체": "042700",
    "리노공업": "058470",
    "엔비디아": "NVDA",
    # 금융/제조 대형주
    "현대모비스": "012330",
    "LG전자": "066570",
    "삼성물산": "028260",
    # 항공
    "진에어": "272450", "대한항공": "003490", "아시아나": "020560",
}

# 동적 유니버스 조회 실패 시 폴백용 정적 리스트 (ticker, name).
# 네트워크/FDR 장애로 유니버스가 빈 리스트가 되면 그날 매매가 조용히 중단되므로,
# 실패 시에는 반드시 이 리스트로 폴백한다.
FALLBACK_TICKERS = [
    ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("005380", "현대차"),
    ("035420", "NAVER"), ("035720", "카카오"), ("000270", "기아"),
    ("005490", "POSCO홀딩스"), ("105560", "KB금융"), ("068270", "셀트리온"),
    ("000810", "삼성화재"), ("051910", "LG화학"), ("032830", "삼성생명"),
    ("015760", "한국전력"), ("033780", "KT&G"), ("003550", "LG"),
    ("000100", "유한양행"), ("373220", "LG에너지솔루션"), ("207940", "삼성바이오로직스"),
    ("055550", "신한지주"), ("012330", "현대모비스"), ("066570", "LG전자"),
    ("028260", "삼성물산"),
]

import logging
_logger = logging.getLogger("StockUniverse")


def get_dynamic_universe(top_n=30):
    """
    실시간으로 KOSPI 시가총액 상위 종목을 추출하여 반환합니다.
    (우선주, 스팩, 리츠 제외) 조회 실패 시 FALLBACK_TICKERS를 반환합니다.

    주의: 네트워크 호출이므로 모듈 import 시점이 아니라 전략 실행 시점에 호출할 것.
    """
    try:
        import FinanceDataReader as fdr
        df = fdr.StockListing('KOSPI')
        # 우선주 제외 (코드가 0으로 끝나는 본주만)
        df = df[df['Code'].str.endswith('0')]
        # 스팩, 리츠 제외
        df = df[~df['Name'].str.contains('스팩|리츠')]

        df_sorted = df.sort_values(by='Marcap', ascending=False).head(top_n)

        tickers = []
        for _, row in df_sorted.iterrows():
            ticker, name = row['Code'], row['Name']
            tickers.append((ticker, name))
            STOCK_MAP[name] = ticker # STOCK_MAP에도 동적 추가

        if not tickers:
            raise ValueError("동적 유니버스 결과가 비어있음")
        return tickers
    except Exception as e:
        _logger.warning(f"⚠️ 동적 유니버스 조회 실패 — 정적 폴백 리스트({len(FALLBACK_TICKERS)}종목) 사용: {e}")
        return list(FALLBACK_TICKERS)

# 전략 엔진용 타겟 종목 리스트.
# import 시점에는 네트워크를 타지 않는 정적 리스트를 노출하고,
# 실시간 Top 30은 StrategyEngine이 실행 시점에 get_dynamic_universe()로 갱신한다.
SAMPLE_TICKERS = list(FALLBACK_TICKERS)
