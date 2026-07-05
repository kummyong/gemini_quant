import os
import sys

from stock_trader.data.dart_api import DartAPI

try:
    dart = DartAPI()
    print(f'Corp mapping count: {len(dart.corp_code_map)}')
    print(f'Samsung Corp Code: {dart.get_corp_code("005930")}')
except Exception as e:
    print(f"Error: {e}")
