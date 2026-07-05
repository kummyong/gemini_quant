"""
gemini_quant 공용 경로 설정 모듈
──────────────────────────────
모든 하위 모듈은 이 파일에서 경로를 import하여 사용합니다.
배포 경로가 변경되더라도 이 파일만 수정하면 됩니다.
"""
import os

# stock_trader/ 디렉토리 (이 파일이 위치한 곳)
STOCK_TRADER_DIR = os.path.dirname(os.path.abspath(__file__))

# 프로젝트 루트 (gemini-quant/)
PROJECT_DIR = os.path.dirname(STOCK_TRADER_DIR)

# secretary/ 디렉토리
SECRETARY_DIR = os.path.join(PROJECT_DIR, "secretary")

# 로그 디렉토리
LOG_DIR = os.path.join(STOCK_TRADER_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 공용 DB 경로
DB_PATH = os.path.join(LOG_DIR, "system_monitor.db")

# 학습 모델 경로
MODEL_PATH = os.path.join(LOG_DIR, "intent_model.pkl")

# 런타임 데이터 디렉토리
RUNTIME_DIR = os.path.join(STOCK_TRADER_DIR, "runtime")
os.makedirs(RUNTIME_DIR, exist_ok=True)

# 전략 프로파일 정의
STOCK_MULTIFACTOR = "STOCK_MULTIFACTOR"
ETF_TREND = "ETF_TREND"

# 현재 활성 전략 프로파일 (환경 변수에서 불러오거나 기본값으로 STOCK_MULTIFACTOR 지정)
ACTIVE_STRATEGY_PROFILE = os.environ.get("STRATEGY_PROFILE", STOCK_MULTIFACTOR)

# ETF 유니버스 리스트 (ticker, name)
ETF_UNIVERSE = [
    ("069500", "KODEX 200"),
    ("133690", "TIGER 미국나스닥100"),
    ("360750", "TIGER 미국S&P500"),
    ("132030", "KODEX 골드선물(H)"),
    ("114260", "KODEX 국고채3년"),
    ("329200", "TIGER 리츠부동산인프라")
]

