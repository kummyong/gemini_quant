from abc import ABC, abstractmethod
from typing import Dict, List

class BrokerInterface(ABC):
    """
    모든 증권사 연동 어댑터가 준수해야 하는 공통 추상 인터페이스입니다.
    """
    
    @abstractmethod
    def get_account_summary(self) -> Dict:
        """
        계좌 잔고 요약 및 보유 종목 현황을 조회합니다.
        반환 규격:
        {
            "tot_pur_amt": str,         # 총 매입금액
            "tot_evlt_amt": str,        # 총 평가금액
            "tot_prft_rt": str,         # 총 수익률
            "prsm_dpst_aset_amt": str,  # 예수금
            "acnt_evlt_remn_indv_tot": List[Dict] # 보유 종목 상세
        }
        """
        pass
        
    @abstractmethod
    def place_order(self, stock_code: str, quantity: int, price: float, side: str) -> Dict:
        """
        주문을 접수합니다.
        side: "BUY" 또는 "SELL"
        """
        pass
        
    @abstractmethod
    def cancel_order(self, order_no: str) -> bool:
        """
        미체결 주문을 취소합니다.
        """
        pass
        
    @abstractmethod
    def get_current_price(self, stock_code: str) -> float:
        """
        종목의 현재가를 실시간으로 가져옵니다.
        """
        pass
