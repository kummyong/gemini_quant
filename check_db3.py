import sqlite3

db_path = r'D:\workspace_py\gemini_quant\stock_trader\logs\system_monitor.db'
conn = sqlite3.connect(db_path)
conn.text_factory = lambda b: b.decode('utf-8', errors='replace')
cur = conn.cursor()

# trade_history 컬럼 확인
cur.execute("PRAGMA table_info(trade_history)")
cols_info = cur.fetchall()
print("=== trade_history 컬럼 ===")
for r in cols_info:
    print(r)

date_col = None
for c in cols_info:
    if 'date' in c[1].lower() or 'time' in c[1].lower() or 'at' in c[1].lower():
        date_col = c[1]
        break

if date_col:
    cur.execute(f"SELECT * FROM trade_history ORDER BY {date_col} DESC LIMIT 10")
else:
    cur.execute("SELECT * FROM trade_history LIMIT 10")

rows = cur.fetchall()
cols = [d[0] for d in cur.description]
print("\n=== 거래 내역 ===")
print(" | ".join(cols))
for r in rows:
    print(r)

# ohlcv 컬럼 확인
cur.execute("PRAGMA table_info(ohlcv_data)")
print("\n=== ohlcv_data 컬럼 ===")
for r in cur.fetchall():
    print(r)

cur.execute("SELECT * FROM ohlcv_data ORDER BY date DESC LIMIT 20")
rows = cur.fetchall()
cols = [d[0] for d in cur.description]
print("\n=== OHLCV 데이터 ===")
print(" | ".join(cols))
for r in rows:
    print(r)

conn.close()
