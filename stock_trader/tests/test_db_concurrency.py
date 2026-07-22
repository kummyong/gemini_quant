import os
import sqlite3
import threading
import time
import pytest
from stock_trader.data.db_repository import DbRepository, _is_lock_error, with_db_retry


def test_is_lock_error_detection():
    locked_err = sqlite3.OperationalError("database is locked")
    busy_err = sqlite3.OperationalError("database is busy")
    other_op_err = sqlite3.OperationalError("no such table: dummy")
    integrity_err = sqlite3.IntegrityError("UNIQUE constraint failed")

    assert _is_lock_error(locked_err) is True
    assert _is_lock_error(busy_err) is True
    assert _is_lock_error(other_op_err) is False
    assert _is_lock_error(integrity_err) is False


def test_non_lock_error_raises_immediately(tmp_path):
    db_file = tmp_path / "test_non_lock.db"
    repo = DbRepository(str(db_file))

    # CHECK constraint violation (quantity < 0) or action constraint error
    calls = 0

    @with_db_retry(max_retries=3, base_delay=0.01)
    def failing_func():
        nonlocal calls
        calls += 1
        raise sqlite3.OperationalError("syntax error near SELECT")

    with pytest.raises(sqlite3.OperationalError) as exc_info:
        failing_func()

    # Must fail on attempt 1 without retrying
    assert calls == 1
    assert "syntax error" in str(exc_info.value)


def test_db_retry_recovers_from_concurrent_lock(tmp_path):
    db_file = tmp_path / "test_concurrency.db"
    repo = DbRepository(str(db_file))

    lock_acquired_event = threading.Event()
    lock_released_event = threading.Event()

    def hold_exclusive_lock():
        conn = sqlite3.connect(str(db_file), timeout=0.1)
        conn.execute("BEGIN EXCLUSIVE TRANSACTION;")
        conn.execute("INSERT OR REPLACE INTO strategy_hyperparams (param_key, param_value) VALUES ('LOCK_TEST', 1.0);")
        lock_acquired_event.set()
        time.sleep(0.3)
        conn.commit()
        conn.close()
        lock_released_event.set()

    lock_thread = threading.Thread(target=hold_exclusive_lock)
    lock_thread.start()

    lock_acquired_event.wait(timeout=2.0)

    # Now repo writer attempts write while exclusive lock is held
    start_time = time.time()
    signal_id = repo.save_trade_signal(
        ticker="005930",
        name="삼성전자",
        action="BUY",
        quantity=10,
        reason="CONCURRENCY_TEST",
        status="PENDING",
        broker_id="KIWOOM"
    )
    elapsed = time.time() - start_time

    lock_thread.join()

    # Verify write succeeded after lock was released
    assert signal_id > 0
    pending_signals = repo.get_pending_signals()
    assert any(s["id"] == signal_id for s in pending_signals)
    assert elapsed >= 0.2  # Waited for lock release and retried successfully


def test_multithreaded_concurrent_writes(tmp_path):
    db_file = tmp_path / "test_multi_thread.db"
    repo = DbRepository(str(db_file))

    errors = []
    num_threads = 5
    items_per_thread = 4

    def worker(thread_idx):
        try:
            for i in range(items_per_thread):
                repo.save_trade_signal(
                    ticker=f"0000{thread_idx}",
                    name=f"종목_{thread_idx}_{i}",
                    action="BUY",
                    quantity=10,
                    reason=f"THREAD_{thread_idx}_ITEM_{i}"
                )
                repo.upsert_hyperparam(f"PARAM_{thread_idx}_{i}", float(i))
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent writes failed with errors: {errors}"
    signals = repo.get_pending_signals()
    assert len(signals) == num_threads * items_per_thread
