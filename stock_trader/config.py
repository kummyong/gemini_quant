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

# 섹터 수급 로테이션 신호(core/sector_flow.py)를 industry_score에 병합할지 여부.
# 초기에는 False로 두고 며칠간 로그(매칭률/z분포)만 관찰한 뒤 켜는 것을 권장한다.
ENABLE_SECTOR_FLOW_BLEND = os.environ.get("ENABLE_SECTOR_FLOW_BLEND", "false").lower() == "true"

# 스마트 머니(수급) 매집 보너스 — 내부자 장내매수 + 외인/기관 수급 연속성/OBV.
# 가점은 scorer.SMART_MONEY_BONUS_CAP으로 캡되어 기존 기술 보너스와 같은 급으로 제한되며,
# 예측력은 진입 스냅샷 → trade_outcomes → 주간 IC 리포트로 계속 검증한다.
ENABLE_SMART_MONEY_BONUS = os.environ.get("ENABLE_SMART_MONEY_BONUS", "true").lower() == "true"

# 센티먼트/미시구조 보너스 (토론방 트래픽, 체결강도, 호가잔량) — 수급 계열과 분리된 플래그.
# 현재 신호 정의에 시점 불일치가 있어(08:30 개장 전 실행 vs 장중 실시간 지표) 재설계 전까지 비활성화.
ENABLE_SENTIMENT_MICRO_BONUS = os.environ.get("ENABLE_SENTIMENT_MICRO_BONUS", "false").lower() == "true"

# ETF 유니버스 리스트 (ticker, name)
ETF_UNIVERSE = [
    ("069500", "KODEX 200"),
    ("133690", "TIGER 미국나스닥100"),
    ("360750", "TIGER 미국S&P500"),
    ("132030", "KODEX 골드선물(H)"),
    ("114260", "KODEX 국고채3년"),
    ("329200", "TIGER 리츠부동산인프라")
]

