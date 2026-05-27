"""check_balance.py

Utility script to fetch and display account balances and holdings across all configured brokers.

Usage:
    python check_balance.py
"""

import sys
import logging
from pathlib import Path

# Ensure the project root is in PYTHONPATH
project_root = Path(__file__).resolve().parent
sys.path.append(str(project_root))

from broker_factory import BrokerFactory

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger('CheckBalance')

def main():
    active_brokers = BrokerFactory.get_active_brokers()
    logger.info(f"Fetching balances for brokers: {active_brokers}")
    total_balance = 0.0
    for broker_name in active_brokers:
        try:
            broker = BrokerFactory.get_broker(broker_name)
            summary = broker.get_account_summary()
            # Parse numeric fields safely
            cash = float(summary.get('prsm_dpst_aset_amt', 0))
            total_assets = float(summary.get('tot_evlt_amt', 0))
            profit_rate = float(summary.get('tot_prft_rt', 0))
            logger.info(
                f"[{broker_name}] Cash: {cash:,.0f} KRW, "
                f"Total Eval: {total_assets:,.0f} KRW, "
                f"Profit Rate: {profit_rate:.2f}%"
            )
            total_balance += total_assets
        except Exception as e:
            logger.error(f"Error fetching data from {broker_name}: {e}")
    logger.info(f"Aggregated portfolio value across all brokers: {total_balance:,.0f} KRW")

if __name__ == '__main__':
    main()
