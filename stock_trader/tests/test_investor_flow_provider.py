import unittest
from unittest.mock import MagicMock

from stock_trader.data.investor_flow_provider import InvestorFlowProvider


def _kiwoom_row(dt, frgnr, orgn, cur_prc="+70000"):
    return {"dt": dt, "frgnr_invsr": frgnr, "orgn": orgn, "cur_prc": cur_prc}


class TestInvestorFlowProvider(unittest.TestCase):
    def setUp(self):
        self.naver = MagicMock()
        self.naver.fetch_investor_net_buying.return_value = (12.3, 70000.0, 2)

    def test_falls_back_to_naver_when_no_token(self):
        kiwoom = MagicMock()
        kiwoom.access_token = None
        provider = InvestorFlowProvider(self.naver, kiwoom_api=kiwoom)

        result = provider.fetch("005930", "삼성전자", 70000.0)

        self.assertEqual(result, (12.3, 70000.0, 2))
        self.naver.fetch_investor_net_buying.assert_called_once()
        kiwoom.get_stock_investor_netbuy.assert_not_called()

    def test_kiwoom_path_parses_amount_and_streak(self):
        kiwoom = MagicMock()
        kiwoom.access_token = "token"
        # 최근일부터 내림차순: +2일 연속 순매수 후 순매도 (천원 단위 금액)
        kiwoom.get_stock_investor_netbuy.return_value = {
            "stk_invsr_orgn": [
                _kiwoom_row("20260717", "30000", "20000"),   # +5천만원
                _kiwoom_row("20260716", "10000", "-5000"),   # +5백만원
                _kiwoom_row("20260715", "-10000", "-10000"), # 순매도 -> 연속 중단
                _kiwoom_row("20260714", "50000", "0"),
                _kiwoom_row("20260711", "1000", "1000"),
            ]
        }
        provider = InvestorFlowProvider(self.naver, kiwoom_api=kiwoom)

        net_eok, price, streak = provider.fetch("005930", "삼성전자", 70000.0)

        # 합계(천원): 50000+5000-20000+50000+2000 = 87000천원 = 0.87억원
        self.assertAlmostEqual(net_eok, 0.87)
        self.assertEqual(price, 70000.0)
        self.assertEqual(streak, 2)
        self.naver.fetch_investor_net_buying.assert_not_called()

    def test_kiwoom_failure_returns_neutral_not_naver(self):
        """키움 소스로 고정된 뒤 개별 조회 실패 시 소스를 섞지 않고 중립값을 반환해야 한다."""
        kiwoom = MagicMock()
        kiwoom.access_token = "token"
        kiwoom.get_stock_investor_netbuy.side_effect = Exception("rate limit")
        provider = InvestorFlowProvider(self.naver, kiwoom_api=kiwoom)

        net_eok, price, streak = provider.fetch("005930", "삼성전자", 70000.0)

        self.assertEqual((net_eok, price, streak), (0.0, 70000.0, 0))
        self.naver.fetch_investor_net_buying.assert_not_called()

    def test_source_fixed_per_run(self):
        """첫 조회에서 소스가 결정되면 이후 호출도 같은 소스를 사용한다."""
        kiwoom = MagicMock()
        kiwoom.access_token = "token"
        kiwoom.get_stock_investor_netbuy.return_value = {
            "stk_invsr_orgn": [_kiwoom_row("20260717", "1000", "0")]
        }
        provider = InvestorFlowProvider(self.naver, kiwoom_api=kiwoom)

        provider.fetch("005930", "삼성전자", 70000.0)
        provider.fetch("000660", "SK하이닉스", 180000.0)

        self.assertEqual(kiwoom.get_stock_investor_netbuy.call_count, 2)
        self.naver.fetch_investor_net_buying.assert_not_called()

    def test_empty_response_returns_neutral(self):
        kiwoom = MagicMock()
        kiwoom.access_token = "token"
        kiwoom.get_stock_investor_netbuy.return_value = {"stk_invsr_orgn": []}
        provider = InvestorFlowProvider(self.naver, kiwoom_api=kiwoom)

        self.assertEqual(provider.fetch("005930", "삼성전자", 70000.0), (0.0, 70000.0, 0))


if __name__ == "__main__":
    unittest.main()
