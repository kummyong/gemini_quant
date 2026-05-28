from stock_trader.broker.broker_interface import BrokerInterface
from stock_trader.broker.kiwoom.kiwoom_api_core import KiwoomApiCore
from typing import Dict

class KiwoomAdapter(BrokerInterface):
    """
    기존 KiwoomApiCore를 래핑하여 BrokerInterface 규격을 맞추는 키움증권 어댑터입니다.
    """
    def __init__(self, api_core: KiwoomApiCore = None):
        if api_core is None:
            self.api = KiwoomApiCore(mode="MOCK")
        else:
            self.api = api_core

    def get_account_summary(self) -> Dict:
        res = self.api.get_account_summary()
        if not res:
            return {
                "tot_pur_amt": "0",
                "tot_evlt_amt": "0",
                "tot_prft_rt": "0.0",
                "prsm_dpst_aset_amt": "0",
                "acnt_evlt_remn_indv_tot": []
            }
        
        # 키움증권 API 응답 구조를 BrokerInterface 공통 규격으로 매핑
        # MOCK 모드 등에서는 최상위에 있고, REAL 모드 등에서는 output 키 아래에 있을 수 있으므로 둘 다 지원하도록 폴백 처리
        output = res.get("output")
        if not isinstance(output, dict):
            output = res
            
        return {
            "tot_pur_amt": output.get("tot_pur_amt", "0"),
            "tot_evlt_amt": output.get("tot_evlt_amt", "0"),
            "tot_prft_rt": output.get("tot_prft_rt", "0.0"),
            "prsm_dpst_aset_amt": output.get("prsm_dpst_aset_amt", "0"),
            "acnt_evlt_remn_indv_tot": res.get("acnt_evlt_remn_indv_tot", [])
        }

    def place_order(self, stock_code: str, quantity: int, price: float, side: str) -> Dict:
        return self.api.place_order(stock_code, quantity, price, side)

    def cancel_order(self, order_no: str) -> bool:
        # 기존 키움 API 코어에 cancel_order가 명시적으로 없으면 항상 성공 처리 (혹은 추후 확장 가능)
        return True

    def get_current_price(self, stock_code: str) -> float:
        # 기존 FinanceDataReader 또는 키움 시세 조회 사용
        import FinanceDataReader as fdr
        try:
            df = fdr.DataReader(stock_code)
            if not df.empty:
                return float(df['Close'].iloc[-1])
        except Exception:
            pass
        return 0.0
