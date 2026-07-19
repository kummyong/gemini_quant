import logging

logger = logging.getLogger("RiskManager")

class RiskManager:
    """포트폴리오 비중 배분 및 트레일링 스탑 등 리스크 관리를 전담하는 클래스"""
    
    def __init__(self, trailing_min_drop=3.0, trailing_max_drop=15.0, chandelier_atr_mult=2.5, market_regime="NEUTRAL"):
        self.TRAILING_MIN_DROP = trailing_min_drop
        self.TRAILING_MAX_DROP = trailing_max_drop
        self.CHANDELIER_ATR_MULT = chandelier_atr_mult
        self.market_regime = market_regime
        
    def set_market_regime(self, regime: str):
        self.market_regime = regime
        
    def calculate_stops(self, atr_pct: float) -> tuple:
        """
        ATR(Average True Range) 비율을 기반으로 동적 하드 스탑과 트레일링 스탑 라인을 계산.
        반환: (dynamic_hard_stop, dynamic_trailing_stop)
        """
        atr = atr_pct if atr_pct else 3.0
        
        # 하드 스탑: 초기 진입 시 절대 허용 불가선 (최소 3%, 최대 8% 사이, 기본 1.5 ATR)
        dynamic_hard_stop = -max(3.0, min(8.0, 1.5 * atr))
        
        # 트레일링 스탑: 최고점 대비 허용 하락폭
        mult = self.CHANDELIER_ATR_MULT
        max_drop = self.TRAILING_MAX_DROP
        
        if self.market_regime == "BULL":
            # 대세 상승장: 트레일링 스탑 이완 (존버 모드)
            mult = 5.0
            max_drop = 25.0
        elif self.market_regime == "BEAR":
            # 대세 하락장: 트레일링 스탑 타이트하게 (칼손절 방어 모드)
            mult = 1.5
            max_drop = 8.0
            
        dynamic_trailing_stop = max(self.TRAILING_MIN_DROP, min(max_drop, mult * atr))
        
        return dynamic_hard_stop, dynamic_trailing_stop
        
    def calculate_size_factor(self, atr_pct: float, base_weight: float = 1.0) -> float:
        """
        ATR 비율을 기반으로 매수 비중 배수(Size Factor)를 계산.
        반환: 기본 예산 대비 승수
        """
        atr = atr_pct if atr_pct else 3.0
        size_factor = max(0.5, min(1.5, 3.0 / atr)) * base_weight
        
        if self.market_regime == "BULL":
            # 대세 상승장: 매수 비중 상향 (1.5배 레버리지 팩터)
            size_factor *= 1.5
        elif self.market_regime == "BEAR":
            # 대세 하락장: 매수 비중 하향 (절반 수준으로 방어적 운용)
            size_factor *= 0.5
            
        return size_factor
