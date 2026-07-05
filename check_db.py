import sqlite3

db_path = r'D:\workspace_py\gemini_quant\stock_trader\logs\system_monitor.db'
conn = sqlite3.connect(db_path)
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [t[0] for t in cur.fetchall()]
print("=== 테이블 목록 ===")
for t in tables:
    print(t)

if 'trade_signals' in tables:
    cur.execute("SELECT * FROM trade_signals ORDER BY created_at DESC LIMIT 20")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    print("\n=== 최근 매매 신호 (최근 20건) ===")
    print(" | ".join(cols))
    for r in rows:
        print(r)

if 'portfolio_status' in tables:
    cur.execute("SELECT * FROM portfolio_status ORDER BY updated_at DESC LIMIT 10")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    print("\n=== 포트폴리오 현황 ===")
    print(" | ".join(cols))
    for r in rows:
        print(r)

if 'trade_history' in tables:
    cur.execute("SELECT * FROM trade_history ORDER BY executed_at DESC LIMIT 10")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    print("\n=== 거래 내역 (최근 10건) ===")
    print(" | ".join(cols))
    for r in rows:
        print(r)

if 'market_snapshot' in tables:
    cur.execute("SELECT * FROM market_snapshot ORDER BY timestamp DESC LIMIT 5")
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    print("\n=== 시장 스냅샷 (최근 5건) ===")
    print(" | ".join(cols))
    for r in rows:
        print(r)

conn.close()
