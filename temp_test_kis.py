import sys
sys.path.append('D:/workspace_py/gemini_quant')
from stock_trader.mock_adapters import KoreaInvestmentAdapter

# Test mock mode
adapter = KoreaInvestmentAdapter(use_mock=True)
print('Mock summary:', adapter.get_account_summary())
# Test real mode (should fallback to mock if env missing)
adapter_real = KoreaInvestmentAdapter(use_mock=False)
print('Real mode summary (fallback):', adapter_real.get_account_summary())
