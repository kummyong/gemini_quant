"""
모바일 최적화 주말 자가 학습 엔진 (trainer.py)
─────────────────────────────────────────────────
안드로이드 Termux(갤럭시 S22 울트라) 환경에 맞춘 경량화 아키텍처 옵티마이저입니다.
- RAM 보호: 전체 데이터가 아닌 10개 종목 랜덤 샘플링 및 최근 1개월 백테스트 제한.
- 발열 제어: 연산 루프 중간에 time.sleep(0.1)을 주어 CPU Throttling 방지.
- 평일 매매 기준 자동 최적화 및 DB 반영.
"""

import os
import sys
import sqlite3
import random
import time
import datetime
import logging
from typing import Dict, List, Tuple
import pandas as pd
import numpy as np

# 경로 및 라이브러리 설정
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from config import DB_PATH
from stock_universe import SAMPLE_TICKERS

try:
    import FinanceDataReader as fdr
except ImportError:
    print("❌ FinanceDataReader 패키지가 필요합니다. 'pip install finance-datareader'를 실행하세요.")
    sys.exit(1)

# 로깅 설정 (KST 기준)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, "logs", "trainer.log"), encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("Trainer")


def load_current_params() -> Dict[str, float]:
    """DB에서 현재 하이퍼파라미터를 로드합니다."""
    params = {
        "RSI_BUY_THRES": 30.0,
        "RSI_SELL_THRES": 70.0,
        "BB_STD": 2.0,
        "TRAILING_STOP_DROP": 3.0,
        "HARD_STOP_LOSS": -5.0
    }
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT param_key, param_value FROM strategy_hyperparams")
            rows = cursor.fetchall()
            for row in rows:
                params[row[0]] = float(row[1])
        logger.info(f"📊 현재 DB 하이퍼파라미터: {params}")
    except Exception as e:
        logger.error(f"⚠️ 파라미터 로드 중 오류 (기본값 사용): {e}")
    return params


def generate_mutants(current: Dict[str, float]) -> List[Dict[str, float]]:
    """현재 파라미터 기준으로 ±1~5 범위 내에서 단 3개의 경량 변종 파라미터 후보를 생성합니다."""
    mutants = []
    
    # 후보 1: 매수선을 높이고 매도선을 낮춤 (더 잦은 거래 지향형)
    m1 = current.copy()
    m1["RSI_BUY_THRES"] = min(45.0, max(20.0, current["RSI_BUY_THRES"] + random.uniform(1.0, 5.0)))
    m1["RSI_SELL_THRES"] = min(80.0, max(55.0, current["RSI_SELL_THRES"] - random.uniform(1.0, 5.0)))
    m1["BB_STD"] = min(2.5, max(1.5, current["BB_STD"] - random.uniform(0.1, 0.3)))
    mutants.append(m1)
    
    # 후보 2: 매수선을 낮추고 매도선을 높임 (보수적 거래 지향형)
    m2 = current.copy()
    m2["RSI_BUY_THRES"] = min(45.0, max(20.0, current["RSI_BUY_THRES"] - random.uniform(1.0, 5.0)))
    m2["RSI_SELL_THRES"] = min(80.0, max(55.0, current["RSI_SELL_THRES"] + random.uniform(1.0, 5.0)))
    m2["BB_STD"] = min(2.5, max(1.5, current["BB_STD"] + random.uniform(0.1, 0.3)))
    mutants.append(m2)
    
    # 후보 3: 손절매 및 익절선 변이 (리스크 관리 변형)
    m3 = current.copy()
    m3["RSI_BUY_THRES"] = min(45.0, max(20.0, current["RSI_BUY_THRES"] + random.uniform(-2.0, 2.0)))
    m3["TRAILING_STOP_DROP"] = min(5.0, max(1.5, current["TRAILING_STOP_DROP"] + random.uniform(-1.0, 1.0)))
    m3["HARD_STOP_LOSS"] = min(-3.0, max(-10.0, current["HARD_STOP_LOSS"] + random.uniform(-1.5, 1.5)))
    mutants.append(m3)
    
    logger.info(f"🧬 변이 파라미터 3종 생성 완료")
    for i, m in enumerate(mutants):
        logger.info(f"   └ 후보 {i+1}: RSI_BUY={m['RSI_BUY_THRES']:.1f}, RSI_SELL={m['RSI_SELL_THRES']:.1f}, BB_STD={m['BB_STD']:.2f}")
    return mutants


def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    """RSI 평가지표 계산 헬퍼 함수"""
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs = gain / (loss + 1e-9)
    return 100 - (100 / (1 + rs))


def run_lite_backtest(tickers: List[Tuple[str, str]], params: Dict[str, float]) -> Tuple[float, float]:
    """
    모바일 RAM 방어를 위해 랜덤 샘플링된 종목에 대해 최근 1개월 백테스팅을 집행합니다.
    - CPU 발열 제어: 루프 돌 때마다 time.sleep(0.1) 강제 삽입
    """
    now = datetime.datetime.now()
    # 최근 1개월 백테스트를 수행하지만, 지표 계산(BB 20, RSI 14)용 버퍼 포함 60일 이전부터 데이터 조회
    start_date = (now - datetime.timedelta(days=60)).strftime('%Y-%m-%d')
    end_date = now.strftime('%Y-%m-%d')
    
    total_yield = 0.0
    mdds = []
    
    for ticker, name in tickers:
        # 모바일 CPU 발열(Overheating) 제어를 위한 정중한 휴식기 제공 (매우 중요)
        time.sleep(0.1)
        
        try:
            df = fdr.DataReader(ticker, start=start_date, end=end_date)
            if df.empty or len(df) < 30:
                continue
            
            # 기술 지표 계산
            df['RSI'] = calculate_rsi(df['Close'], 14)
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['STD20'] = df['Close'].rolling(window=20).std()
            df['BB_LOWER'] = df['MA20'] - params["BB_STD"] * df['STD20']
            df['BB_UPPER'] = df['MA20'] + params["BB_STD"] * df['STD20']
            
            # 최근 1개월간의 백테스팅 진행 (앞선 30영업일은 지표 빌드업 버퍼로 소모)
            test_df = df.iloc[25:]
            
            position = 0  # 0: 무포지션, 1: 보유중
            buy_price = 0.0
            peak_price = 0.0
            stock_yields = []
            
            for i in range(len(test_df)):
                row = test_df.iloc[i]
                current_price = row['Close']
                rsi_val = row['RSI']
                lower_band = row['BB_LOWER']
                upper_band = row['BB_UPPER']
                
                # 지표 결측값 예외처리
                if pd.isna(rsi_val) or pd.isna(lower_band) or pd.isna(upper_band):
                    continue
                
                # 보유 중일 때 리스크 관리 및 매도 로직
                if position == 1:
                    peak_price = max(peak_price, current_price)
                    profit_rate = ((current_price - buy_price) / buy_price) * 100
                    peak_drawdown = ((current_price - peak_price) / peak_price) * 100
                    
                    # 1. Hard Stop Loss
                    # 2. Trailing Stop Loss
                    # 3. RSI 오버슈팅 / BB 상단 이탈
                    if (profit_rate <= params["HARD_STOP_LOSS"] or 
                        (peak_price > buy_price * 1.02 and peak_drawdown <= -params["TRAILING_STOP_DROP"]) or
                        rsi_val >= params["RSI_SELL_THRES"] or 
                        current_price >= upper_band):
                        
                        # 청산 수익률 기록
                        stock_yields.append(profit_rate)
                        position = 0
                        buy_price = 0.0
                
                # 미보유 중일 때 매수 로직
                else:
                    if rsi_val <= params["RSI_BUY_THRES"] or current_price <= lower_band:
                        position = 1
                        buy_price = current_price
                        peak_price = current_price
            
            # 테스트 종료 시점에 남은 포지션 강제 청산 평가
            if position == 1:
                last_price = test_df.iloc[-1]['Close']
                profit_rate = ((last_price - buy_price) / buy_price) * 100
                stock_yields.append(profit_rate)
            
            # 개별 종목 성과 합산
            if stock_yields:
                cum_return = np.sum(stock_yields)  # 가단순 누적
                total_yield += cum_return
                # 단순 종목별 최악의 낙폭을 MDD 대용으로 사용
                mdds.append(abs(min(stock_yields)) if min(stock_yields) < 0 else 0.1)
            else:
                mdds.append(0.1)
                
        except Exception as e:
            continue
            
    # 전체 포트폴리오 차원의 성과 지표 요약
    avg_mdd = np.mean(mdds) if mdds else 1.0
    return total_yield, avg_mdd


def update_best_params(best_params: Dict[str, float]):
    """평가지표 1위인 우수 유전자로 DB를 업데이트합니다."""
    try:
        with sqlite3.connect(DB_PATH, timeout=30.0) as conn:
            cursor = conn.cursor()
            for key, val in best_params.items():
                cursor.execute("""
                    INSERT INTO strategy_hyperparams (param_key, param_value, updated_at)
                    VALUES (?, ?, datetime('now', 'localtime'))
                    ON CONFLICT(param_key) DO UPDATE SET
                        param_value = excluded.param_value,
                        updated_at = excluded.updated_at
                """, (key, val))
            conn.commit()
        logger.info(f"✨ 최적 하이퍼파라미터 DB 업데이트 완료!")
    except Exception as e:
        logger.error(f"❌ DB 업데이트 실패: {e}")


def main():
    logger.info("🎬 [Self-Learning] 모바일 최적화 주말 자가 학습 프로세스 가동")
    
    # 1. 기존 파라미터 로드
    curr_params = load_current_params()
    
    # 2. 유전자 변이 3종 생성
    candidates = generate_mutants(curr_params)
    # 현재 파라미터도 비교군에 포함하여 퇴보 방지
    candidates.append(curr_params)
    
    # 3. 우량 유니버스 중 딱 10개만 무작위 샘플링 (모바일 RAM 및 연산 최소화 핵심 설계)
    universe = SAMPLE_TICKERS.copy()
    sampled_tickers = random.sample(universe, min(len(universe), 10))
    logger.info(f"🎲 샘플링된 10종목: {[name for _, name in sampled_tickers]}")
    
    # 4. 후보군 백테스트 수행
    best_candidate = curr_params
    best_score = -999999.0
    best_metrics = (0.0, 1.0)
    
    for i, cand in enumerate(candidates):
        is_curr = " (현재 설정)" if i == 3 else f" (후보 {i+1})"
        logger.info(f"🏃 {is_curr} 백테스트 구동 중...")
        
        tot_return, mdd = run_lite_backtest(sampled_tickers, cand)
        # 평가지표: 총수익률 / MDD (위험대비 성과 지표)
        score = tot_return / max(0.1, mdd)
        
        logger.info(f"   📊 결과: 누적수익률={tot_return:+.2f}%, 평균MDD={mdd:.2f}%, 점수(Return/MDD)={score:.2f}")
        
        if score > best_score:
            best_score = score
            best_candidate = cand
            best_metrics = (tot_return, mdd)
            
    # 5. DB 업데이트 및 자가학습 완료 로깅
    update_best_params(best_candidate)
    
    # [요구사항 준수] 지정 포맷에 정확히 부합하는 KST 로깅 출력
    logger.info(
        f"🏆 이번 주 자가 학습 완료. 발열 제어 모드 정상 작동. "
        f"새로운 매수 기준: RSI {best_candidate['RSI_BUY_THRES']:.1f} / "
        f"매도 기준: {best_candidate['RSI_SELL_THRES']:.1f} / "
        f"BB_STD: {best_candidate['BB_STD']:.2f} "
        f"(예상 누적수익률: {best_metrics[0]:+.2f}%, MDD: {best_metrics[1]:.2f}%)"
    )


if __name__ == "__main__":
    main()
