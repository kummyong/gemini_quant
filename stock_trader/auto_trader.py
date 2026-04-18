import os
import sys
import time
import logging
import json
import math
import requests
import fcntl
import sqlite3
import signal
import functools
from datetime import datetime
from dotenv import load_dotenv

# 지수 백오프 재시도 데코레이터
def retry_with_backoff(max_retries=5, base_delay=2):
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
                        logging.critical(f"🔥 [CRITICAL] 재시도 횟수 초과. 프로세스 종료 및 재시작 유도.")
                        send_telegram_message(f"🚨 *[긴급]* 네트워크 장애로 인해 매매 엔진이 종료되었습니다. (5회 재시도 실패)\n- 에러: {e}")
                        sys.exit(1) # Runit 등에 의해 자동 재시작됨
                    
                    time.sleep(delay)
                except Exception as e:
                    logging.error(f"❌ [Logic Error] {func.__name__} 오류: {e}")
                    raise e
            return None
        return wrapper
    return decorator

# 경로 설정
BASE_DIR = "/root/workspace/gemini-quant/stock_trader"
LOG_DIR = os.path.join(BASE_DIR, 'logs')
if not os.path.exists(LOG_DIR): os.makedirs(LOG_DIR)
LOG_PATH = os.path.join(LOG_DIR, 'auto_trader.log')
SIGNAL_PATH = os.path.join(BASE_DIR, 'signals.json')
DB_PATH = os.path.join(LOG_DIR, "system_monitor.db")

# 중복 실행 방지
LOCK_FILE = os.path.join(LOG_DIR, "auto_trader.lock")
fp = open(LOCK_FILE, 'w')
try:
    fcntl.lockf(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    print("⚠️  Auto Trader is already running. Exiting...")
    sys.exit(0)

# Kiwoom_MCP_Server 경로 추가
sys.path.append(os.path.join(BASE_DIR, 'Kiwoom_MCP_Server'))
from kiwoom_mcp import KiwoomApiManager, KiwoomConfig

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_PATH, encoding='utf-8'), logging.StreamHandler(sys.stdout)]
)

def send_telegram_message(message):
    load_dotenv(os.path.join(BASE_DIR, '.env'))
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    chat_id = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat_id: return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}, timeout=10)
    except: pass

class AutoTrader:
    def __init__(self, mode='MOCK', dry_run=False):
        self.mode = mode
        self.dry_run = dry_run
        self.config = KiwoomConfig(mode=mode)
        self.api = KiwoomApiManager(self.config)
        self.portfolio = []
        self.cash = 0
        self.total_assets = 0
        self.signals = {"watchlist": []}
        logging.info(f"🚀 [자율 매매 엔진 v3.2 Resilience+] 가동 (모드: {mode})")

    def clean_int(self, val):
        if isinstance(val, str):
            clean_val = val.replace('+', '').replace('-', '').strip()
            return int(clean_val) if clean_val else 0
        return abs(int(val or 0))

    def load_signals(self):
        """DB에서 유효한 매매 신호 로드"""
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute("SELECT ticker, name, action, quantity, reason FROM trade_signals WHERE status = 'PENDING'")
                db_signals = [dict(row) for row in cursor.fetchall()]
                
                if db_signals:
                    self.signals = {"watchlist": db_signals}
                    logging.info(f"🧠 DB 전략 로드 완료 (감시: {len(db_signals)}개)")
                elif os.path.exists(SIGNAL_PATH):
                    # DB에 데이터가 없으면 기존 JSON에서 마이그레이션
                    with open(SIGNAL_PATH, 'r', encoding='utf-8') as f:
                        json_data = json.load(f)
                        self.signals = json_data
                        for target in self.signals.get('watchlist', []):
                            cursor.execute("""
                                INSERT OR IGNORE INTO trade_signals (ticker, name, action, reason) 
                                VALUES (?, ?, ?, ?)
                            """, (target['ticker'], target['name'], target['action'], target['reason']))
                        conn.commit()
                        logging.info("📝 JSON 신호를 DB로 마이그레이션 완료")
        except Exception as e:
            logging.error(f"❌ DB 신호 로드 실패: {e}")

    @retry_with_backoff()
    def update_portfolio(self):
        """실시간 잔고를 가져와 DB에 업데이트"""
        balance_res = self.api.get_account_balance(qry_tp="2")
        if balance_res and balance_res.get("return_code") == 0:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    eval_amt = self.clean_int(balance_res.get("tot_evlt_amt", 0))
                    self.cash = self.clean_int(balance_res.get("prsm_dpst_aset_amt", 0))
                    self.total_assets = eval_amt + self.cash
                    cash_rt = round((self.cash / self.total_assets) * 100, 2) if self.total_assets > 0 else 0
                    
                    # 계좌 요약 저장
                    cursor.execute("""
                        INSERT INTO account_summary (total_assets, cash, cash_ratio) 
                        VALUES (?, ?, ?)
                    """, (self.total_assets, self.cash, cash_rt))

                    # 포트폴리오 상태 갱신
                    cursor.execute("DELETE FROM portfolio_status")
                    raw_portfolio = balance_res.get("acnt_evlt_remn_indv_tot", [])
                    self.portfolio = raw_portfolio # 로직 내부용
                    
                    for item in raw_portfolio:
                        stk_cd = item.get('stk_cd', '')
                        stk_nm = item.get('stk_nm')
                        rmnd_qty = self.clean_int(item.get('rmnd_qty', 0))
                        pur_pric = self.clean_int(item.get('pur_pric', 0))
                        cur_prc = self.clean_int(item.get('cur_prc', 0))
                        prft_rt = float(item.get('prft_rt', 0))
                        pred_sellq = min(self.clean_int(item.get('pred_sellq', 0)), rmnd_qty)
                        tdy_sellq = self.clean_int(item.get('tdy_sellq', 0))
                        
                        cursor.execute("""
                            INSERT INTO portfolio_status (stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, pred_sellq, tdy_sellq)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (stk_cd, stk_nm, rmnd_qty, pur_pric, cur_prc, prft_rt, pred_sellq, tdy_sellq))
                    conn.commit()
                logging.info(f"📋 DB 잔고 업데이트 완료 (자산: {self.total_assets:,}원)")
            except Exception as e:
                logging.error(f"❌ DB 잔고 업데이트 실패: {e}")
        else:
            logging.error(f"❌ 잔고 조회 실패: {balance_res.get('return_msg')}")

    def calculate_position_size(self, ticker, price, stock_name):
        if self.total_assets <= 0 or price <= 0: return 0
        safe_cash_limit = self.total_assets * 0.3
        available_buying_power = self.cash - safe_cash_limit
        if available_buying_power <= 0: return 0
        max_stock_limit = self.total_assets * 0.15
        current_stock_val = 0
        for p in self.portfolio:
            if p.get('stk_cd', '').replace('A', '') == ticker:
                current_stock_val = self.clean_int(p.get('evlt_amt', 0))
                break
        remain_limit = max_stock_limit - current_stock_val
        if remain_limit <= 0: return 0
        target_buy_amt = min(available_buying_power, remain_limit) * 0.5
        return int(target_buy_amt // price)

    @retry_with_backoff()
    def check_market_signals(self):
        # 1. 포트폴리오 기반 매도 체크
        for item in self.portfolio:
            ticker = item.get('stk_cd', '').replace('A', '')
            name = item.get('stk_nm')
            profit_rt = float(item.get('prft_rt', 0))
            cur_price = self.clean_int(item.get('cur_prc', 0))
            total_qty = self.clean_int(item.get('rmnd_qty', 0))
            
            if profit_rt >= 10.0:
                self.execute_order(ticker, 'SELL', total_qty, cur_price, name, f"익절({profit_rt:+.2f}%)")
            elif profit_rt <= -4.0:
                self.execute_order(ticker, 'SELL', total_qty, cur_price, name, f"손절({profit_rt:+.2f}%)")

        # 2. 감시 신호 기반 매수 체크
        for target in self.signals.get('watchlist', []):
            if target.get('action') == 'BUY':
                ticker = str(target['ticker']).replace('A', '')
                stock_info = self.api.get_stock_info(ticker)
                if stock_info and stock_info.get("return_code") == 0:
                    cur_price = self.clean_int(stock_info.get('cur_prc', 0))
                    buy_qty = self.calculate_position_size(ticker, cur_price, target.get('name'))
                    if buy_qty > 0:
                        self.execute_order(ticker, 'BUY', buy_qty, cur_price, target.get('name'), "전략 매수")

    @retry_with_backoff()
    def execute_order(self, ticker, side, quantity, price, stock_name, reason):
        if quantity <= 0: return False
        
        if self.dry_run: return True
            
        res = self.api.place_order(ticker, quantity, price, side=side, ord_tp="03")
        if res and res.get("return_code") == 0:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            msg = f"✅ *[주문 체결]* {stock_name} {quantity}주 {side} 완료!\n- 사유: {reason}"
            send_telegram_message(msg)
            
            # [Step 3] DB 이력 기록 및 상태 전이 (트랜잭션 강화)
            conn = sqlite3.connect(DB_PATH)
            try:
                conn.execute("BEGIN TRANSACTION") # 1. 명시적 트랜잭션 시작
                cursor = conn.cursor()
                
                # 2. 매매 이력(trade_history) 추가
                cursor.execute("""
                    INSERT INTO trade_history (ticker, name, side, quantity, price, amt, reason)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (ticker, stock_name, side, quantity, price, quantity * price, reason))
                
                # 3. 신호 상태(trade_signals) 완료 처리
                cursor.execute("UPDATE trade_signals SET status = 'DONE' WHERE ticker = ?", (ticker,))
                
                conn.commit() # 4. 정상 완료 시 커밋
                logging.info(f"💾 DB 업데이트 성공: {stock_name} ({side})")
            except Exception as e:
                conn.rollback() # 5. 에러 발생 시 롤백 (데이터 원상복구)
                error_msg = f"🚨 *[긴급 DB 에러]* 주문 체결 후 DB 기록 실패 (롤백됨)!\n- 종목: {stock_name}\n- 에러: {e}"
                send_telegram_message(error_msg) # 6. 텔레그램 긴급 알림
                logging.error(f"❌ DB 이력 기록 실패 (Rollback): {e}")
            finally:
                conn.close()
            return True
        return False

    def run(self):
        logging.info("🌟 [v3.0 SQLite-Only] 자율 매매 루프 시작")
        signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))
        signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))

        while True:
            try:
                now = datetime.now()
                if now.weekday() < 5 and (9 <= now.hour < 15 or (now.hour == 15 and now.minute < 30)):
                    self.load_signals()
                    self.update_portfolio()
                    self.check_market_signals()
                    wait_sec = 60
                else:
                    logging.info(f"💤 장외 대기 중... ({now.strftime('%H:%M:%S')})")
                    wait_sec = 600
                sys.stdout.flush()
                time.sleep(wait_sec)
            except Exception as e:
                logging.error(f"❌ 루프 오류: {e}")
                time.sleep(10)

if __name__ == "__main__":
    trader = AutoTrader(mode='MOCK', dry_run=False)
    trader.run()
