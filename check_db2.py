import sqlite3

db_path = r'D:\workspace_py\gemini_quant\stock_trader\logs\system_monitor.db'
conn = sqlite3.connect(db_path)
conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
cur = conn.cursor()

# portfolio_status 컬럼 확인
cur.execute("PRAGMA table_info(portfolio_status)")
print("=== portfolio_status 컬럼 ===")
for r in cur.fetchall():
    print(r)

cur.execute("SELECT * FROM portfolio_status LIMIT 10")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
print("\n=== 포트폴리오 현황 ===")
print(" | ".join(cols))
for r in rows:
    print(r)

# trade_history
cur.execute("SELECT * FROM trade_history ORDER BY executed_at DESC LIMIT 10")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
print("\n=== 거래 내역 (최근 10건) ===")
print(" | ".join(cols))
for r in rows:
    print(r)

# account_summary
cur.execute("SELECT * FROM account_summary ORDER BY updated_at DESC LIMIT 5")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
print("\n=== 계좌 요약 ===")
print(" | ".join(cols))
for r in rows:
    print(r)

# ohlcv_data 최근 RSI 확인
cur.execute("SELECT ticker, date, close, rsi, bb_lower FROM ohlcv_data ORDER BY date DESC LIMIT 30")
rows = cur.fetchall()
print("\n=== 최근 OHLCV/RSI 데이터 ===")
print("ticker | date | close | rsi | bb_lower")
for r in rows:
    print(r)

conn.close()
