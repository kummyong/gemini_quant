import argparse
import time
import logging
from stock_trader.data.db_repository import DbRepository
from stock_trader.config import DB_PATH
from stock_trader.core.stock_universe import SAMPLE_TICKERS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BackfillOHLCV")

# ETF 유니버스 정의
ETF_TICKERS = [
    ("069500", "KODEX 200"),
    ("133690", "TIGER 미국나스닥100"),
    ("360750", "TIGER 미국S&P500"),
    ("132030", "KODEX 골드선물(H)"),
    ("114260", "KODEX 국고채3년"),
    ("329200", "TIGER 리츠부동산인프라")
]

def main():
    parser = argparse.ArgumentParser(description="Backfill OHLCV data to local DB")
    parser.add_argument("--days", type=int, default=1825, help="Number of days to backfill (5 years = 1825)")
    args = parser.parse_args()

    db = DbRepository(DB_PATH)

    # 주식 유니버스 + ETF 유니버스 합치기
    all_tickers = []
    seen = set()
    for ticker, name in SAMPLE_TICKERS + ETF_TICKERS:
        if ticker not in seen:
            seen.add(ticker)
            all_tickers.append((ticker, name))

    logger.info(f"🚀 총 {len(all_tickers)}개 종목에 대해 {args.days}일 치 OHLCV 백필 작업을 시작합니다.")

    for i, (ticker, name) in enumerate(all_tickers):
        logger.info(f"[{i+1}/{len(all_tickers)}] {name} ({ticker}) 동기화 중...")
        try:
            db.sync_ohlcv_data(ticker, force_full=True, backfill_days=args.days)
            logger.info(f"✅ {name} ({ticker}) 동기화 완료")
        except Exception as e:
            logger.error(f"❌ {name} ({ticker}) 동기화 중 오류 발생: {e}")
        
        # 요청 간 0.5초 대기
        time.sleep(0.5)

    logger.info("🎉 모든 종목의 OHLCV 백필 작업이 완료되었습니다.")

if __name__ == "__main__":
    main()
