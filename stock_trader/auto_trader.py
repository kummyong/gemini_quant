import os
import sys
import time
import logging
import json
import fcntl
import sqlite3
import signal
import functools
import pandas as pd
import numpy as np
import requests
from datetime import datetime
from dotenv import load_dotenv

# Kiwoom_MCP_Server 경로 추가 및 모듈 로드
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
sys.path.append(os.path.join(BASE_DIR, 'Kiwoom_MCP_Server'))
from kiwoom_mcp import KiwoomApiManager, KiwoomConfig

# --- 로깅 및 경로 설정 ---
LOG_DIR = os.path.join(BASE_DIR, 'logs')
if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)
LOG_PATH = os.path.join(LOG_DIR, 'auto_trader.log')
DB_PATH = os.path.join(LOG_DIR, "system_monitor.db")
LOCK_FILE = os.path.join(LOG_DIR, "auto_trader.lock")

# 중복 실행 방지 (File Locking)
try:
    fp = open(LOCK_FILE, 'w')
    fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    print("⚠️ Auto Trader is already running. Exiting...")
    sys.exit(0)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_PATH, encoding='utf-8'), logging.StreamHandler(sys.stdout)]
)

# --- 유틸리티 함수 ---
def retry_with_backoff(max_retries=5, base_delay=2):
    """지수 백오프 기반 재시도 데코레이터"""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except (requests.exceptions.RequestException, ConnectionError, TimeoutError) as e:
                    retries += 1
                    delay = base_delay * (2 ** (retries - 1))
                    logging.warning(f"⚠️ [통신 장애] {func.__name__} 실패. {delay}초 후 재시도 ({retries}/{max_retries}) | 에러: {e}")
                    if retries >= max_retries:
                        logging.critical(f"🔥 [CRITICAL] 재시도 횟수 초과. 엔진 종료.")
                        send_telegram_message(f"🚨 *[긴급]* 네트워크 장애로 인해 매매 엔진이 종료되었습니다.\n- 에러: {e}")
                        sys.exit(1)
                    time.sleep(delay)
                except Exception as e:
                    logging.error(f"❌ [Logic Error] {func.__name__} 오류: {e}")
                    raise e
            return None
        return wrapper
    return decorator

def send_telegram_message(message):
    """텔레그램 알림 전송"""
    load_dotenv(os.path.join(BASE_DIR, '.env'))
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id: return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except: pass

# --- 코어 매매 엔진 ---
class AutoTrader:
    def __init__(self, mode='MOCK', dry_run=False):
        self.mode = mode
        self.dry_run = dry_run
        self.config = KiwoomConfig(mode=mode)
        self.api = KiwoomApiManager(self.config)
        self.portfolio = []
        self.cash = 0
        self.total_assets = 0
        # 🛡️ [Portfolio Master Rules]
        self.MAX_HOLDINGS = 5       # 최대 5개 종목 보유 (자본 가동률 상향)
        self.SAFE_CASH_RATIO = 0.2  # 20% 현금 마진 유지
        self.STOCK_LIMIT_RATIO = 0.15 # 종목당 15% 비중 제한
        logging.info(f"🚀 [Master Portfolio Rule v8.0] 가동 (모드: {mode})")

    def clean_int(self, val):
        """API 응답 데이터 정제"""
        if isinstance(val, str):
            clean_val = val.replace('+', '').replace('-', '').replace(',', '').strip()
            return int(clean_val) if clean_val else 0
        return abs(int(val or 0))

    def calculate_swing_indicators(self, df):
        """[Pandas Vectorized] 보조지표 계산 로직"""
        if len(df) < 20: return df
        df['close'] = df['close'].astype(float)
        close = df['close']
        df['MA_5'] = close.rolling(window=5).mean()
        df['MA_20'] = close.rolling(window=20).mean()
        std_20 = close.rolling(window=20).std()
        df['BB_upper'] = df['MA_20'] + (std_20 * 2)
        df['BB_lower'] = df['MA_20'] - (std_20 * 2)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss
        df['RSI_14'] = 100 - (100 / (1 + rs))
        return df

    def generate_trading_signal(self, df):
        """[Swing Strategy] 매매 시그널 판정"""
        if df.empty or 'RSI_14' not in df.columns: return 'HOLD'
        last = df.iloc[-1]
        cur_price = last['close']
        if pd.isna(last['RSI_14']) or pd.isna(last['BB_lower']): return 'HOLD'
        if last['RSI_14'] <= 30 and cur_price <= last['BB_lower']: return 'BUY'
        if last['RSI_14'] >= 70 or cur_price >= last['BB_upper']: return 'SELL'
        return 'HOLD'

    @retry_with_backoff()
    def update_portfolio(self):
        """실시간 잔고 및 포트폴리오 상태 업데이트"""
        balance_res = self.api.get_account_balance(qry_tp="2")
        if balance_res and balance_res.get("return_code") == 0:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    eval_amt = self.clean_int(balance_res.get("tot_evlt_amt", 0))
                    self.cash = self.clean_int(balance_res.get("prsm_dpst_aset_amt", 0))
                    self.total_assets = eval_amt + self.cash
                    cash_rt = round((self.cash / self.total_assets) * 100, 2) if self.total_assets > 0 else 0
                    cursor.execute("INSERT INTO account_summary (total_assets, cash, cash_ratio) VALUES (?, ?, ?)", 
                                 (self.total_assets, self.cash, cash_rt))
                    cursor.execute("DELETE FROM portfolio_status")
                    raw_portfolio = balance_res.get("acnt_evlt_remn_indv_tot", [])
                    self.portfolio = raw_portfolio
                    for item in raw_portfolio:
                        cursor.execute("""
                            INSERT INTO portfolio_status (stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, pred_sellq, tdy_sellq)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (item.get('stk_cd',''), item.get('stk_nm'), self.clean_int(item.get('rmnd_qty')),
                              self.clean_int(item.get('pur_pric')), self.clean_int(item.get('cur_prc')),
                              float(item.get('prft_rt', 0)), self.clean_int(item.get('pred_sellq')), self.clean_int(item.get('tdy_sellq'))))
                    conn.commit()
                logging.info(f"📋 포트폴리오 동기화: {len(self.portfolio)}종목 보유 / 자산 {self.total_assets:,}원")
            except Exception as e:
                logging.error(f"❌ DB 업데이트 실패: {e}")

    def calculate_position_size(self, ticker, price):
        """위험 관리 기반 포지션 사이징 로직"""
        if self.total_assets <= 0 or price <= 0: return 0
        safe_cash_limit = self.total_assets * self.SAFE_CASH_RATIO
        available_cash = self.cash - safe_cash_limit
        if available_cash <= 0: return 0
        
        max_stock_limit = self.total_assets * self.STOCK_LIMIT_RATIO
        current_val = 0
        for p in self.portfolio:
            if p.get('stk_cd', '').replace('A', '') == ticker:
                current_val = self.clean_int(p.get('evlt_amt', 0)) or (self.clean_int(p.get('rmnd_qty', 0)) * self.clean_int(p.get('cur_prc', 0)))
                break
        
        remain_limit = max_stock_limit - current_val
        if remain_limit <= 0: return 0
        
        target_amt = min(available_cash, remain_limit) * 0.5
        return int(target_amt // price)

    @retry_with_backoff()
    def check_market_signals(self):
        """전략 실행 루프 (Fallback Execution 적용)"""
        # 1. 매도 시그널 체크
        for item in self.portfolio:
            ticker = item.get('stk_cd', '').replace('A', '')
            name = item.get('stk_nm')
            qty = self.clean_int(item.get('rmnd_qty', 0))
            if qty <= 0: continue

            time.sleep(1.0) 
            chart_res = self.api.get_daily_chart(ticker, datetime.now().strftime("%Y%m%d"))
            if chart_res and "output" in chart_res:
                df = pd.DataFrame(chart_res["output"]).rename(columns={'stk_clpr': 'close'})
                df = df.iloc[::-1].reset_index(drop=True)
                df = self.calculate_swing_indicators(df)
                signal = self.generate_trading_signal(df)
                if signal == 'SELL':
                    self.execute_order(ticker, 'SELL', qty, df.iloc[-1]['close'], name, "스윙 전략 매도 시그널")

        # 🛡️ 2. [Macro Control] 최대 보유 종목 수 확인 (5종목)
        if len(self.portfolio) >= self.MAX_HOLDINGS:
            logging.info(f"🚫 [신규 매수 보류] 보유 종목 수({len(self.portfolio)})가 한도({self.MAX_HOLDINGS})에 도달했습니다.")
            return

        # 3. 매수 후보군 수집
        buy_candidates = []
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT ticker, name FROM trade_signals WHERE status = 'PENDING'")
                watchlist = [dict(row) for row in cursor.fetchall()]
        except: watchlist = []

        for target in watchlist:
            ticker = target['ticker'].replace('A', '')
            time.sleep(1.0) 
            chart_res = self.api.get_daily_chart(ticker, datetime.now().strftime("%Y%m%d"))
            if chart_res and "output" in chart_res:
                df = pd.DataFrame(chart_res["output"]).rename(columns={'stk_clpr': 'close'})
                df = df.iloc[::-1].reset_index(drop=True)
                df = self.calculate_swing_indicators(df)
                signal = self.generate_trading_signal(df)
                
                if signal == 'BUY':
                    buy_candidates.append({
                        'ticker': ticker,
                        'name': target['name'],
                        'price': df.iloc[-1]['close'],
                        'rsi': df.iloc[-1]['RSI_14']
                    })
                    logging.info(f"🔍 [후보 수집] {target['name']} (RSI: {df.iloc[-1]['RSI_14']:.2f})")

        # 🛡️ 4. [Fallback Execution] 랭킹 순차적 매수 집행
        if buy_candidates:
            buy_candidates.sort(key=lambda x: x['rsi'])
            logging.info(f"📊 [랭킹 평가] {len(buy_candidates)}개 후보 중 최적 종목을 순차적으로 탐색합니다.")
            
            for candidate in buy_candidates:
                buy_qty = self.calculate_position_size(candidate['ticker'], candidate['price'])
                
                if buy_qty > 0:
                    logging.info(f"🏆 [매수 집행] 후보 선정: {candidate['name']} (RSI: {candidate['rsi']:.2f})")
                    if self.execute_order(candidate['ticker'], 'BUY', buy_qty, candidate['price'], candidate['name'], f"랭킹 매수(RSI:{candidate['rsi']:.2f})"):
                        break # 한 사이클에 1종목만 매수
                else:
                    logging.info(f"⏭️ [차순위 이동] {candidate['name']} 매수 불가(비중 한도 초과 등).")
                    continue

    @retry_with_backoff()
    def execute_order(self, ticker, side, quantity, price, stock_name, reason):
        """주문 실행 및 DB 이력 기록"""
        if quantity <= 0: return False
        if self.dry_run:
            logging.info(f"🧪 [DRY RUN] {stock_name} {side} {quantity}주 ({reason})")
            return True
            
        res = self.api.place_order(ticker, quantity, price, side=side, ord_tp="03")
        if res and res.get("return_code") == 0:
            logging.info(f"✅ [주문 성공] {stock_name} {side} {quantity}주 | 사유: {reason}")
            send_telegram_message(f"✅ *[주문 체결]* {stock_name} {quantity}주 {side} 완료!\n- 사유: {reason}")
            
            with sqlite3.connect(DB_PATH) as conn:
                try:
                    conn.execute("BEGIN TRANSACTION")
                    cursor = conn.cursor()
                    cursor.execute("""
                        INSERT INTO trade_history (ticker, name, side, quantity, price, amt, reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (ticker, stock_name, side, quantity, price, quantity * price, reason))
                    cursor.execute("UPDATE trade_signals SET status = 'DONE' WHERE ticker = ?", (ticker,))
                    conn.commit()
                except: conn.rollback()
            return True
        return False

    def run(self, once=False):
        """메인 루프 (15분 주기)"""
        logging.info("🌟 자율 매매 루프 시작 (Master Portfolio Rule v8.0)")
        signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
        signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

        while True:
            try:
                now = datetime.now()
                is_market_open = now.weekday() < 5 and (9 <= now.hour < 15 or (now.hour == 15 and now.minute < 30))
                
                if is_market_open or once:
                    self.update_portfolio()
                    self.check_market_signals()
                    if once: break
                    
                    wait_sec = 900 
                    logging.info(f"⏳ 스캔 완료. 포트폴리오 안정화를 위해 15분(900초) 대기합니다...")
                else:
                    logging.info(f"💤 장외 대기 중... ({now.strftime('%H:%M:%S')})")
                    wait_sec = 900
                sys.stdout.flush()
                time.sleep(wait_sec)
            except Exception as e:
                logging.error(f"❌ 루프 예외 발생: {e}")
                if once: break
                time.sleep(30)

if __name__ == "__main__":
    mode = 'MOCK'
    is_once = '--once' in sys.argv
    trader = AutoTrader(mode=mode, dry_run=True)
    trader.run(once=is_once)
