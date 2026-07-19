import unittest
from unittest.mock import patch

from stock_trader.core.scorer import MultiFactorScorer, SMART_MONEY_BONUS_CAP


def _base_stock(**overrides):
    stock = {
        "ticker": "005930", "name": "삼성전자",
        "eps_growth": 0.0, "industry_score": 50.0, "net_buying": 0.0,
        "dart_revenue_growth": 0.0, "dart_op_growth": 0.0,
        "dart_debt_ratio": 50.0, "dart_cf_quality": 50.0,
    }
    stock.update(overrides)
    return stock


class TestSmartMoneyBonus(unittest.TestCase):
    """수급 계열 가점(내부자/연속매수+OBV)의 크기·캡·플래그 게이팅 검증.

    단일 종목 리스트에서는 min-max 정규화 분모가 1.0으로 고정되어
    가점 외 점수가 결정론적이므로, 가점 유무를 점수 차로 직접 검증한다."""

    def _score(self, stock, smart_money=True, sentiment=False):
        with patch("stock_trader.core.scorer.trader_config") as cfg:
            cfg.ENABLE_SMART_MONEY_BONUS = smart_money
            cfg.ENABLE_SENTIMENT_MICRO_BONUS = sentiment
            return MultiFactorScorer().calculate_scores([stock])[0]["total_score"]

    def test_flag_off_no_bonus(self):
        base = self._score(_base_stock(), smart_money=False)
        with_signals = self._score(
            _base_stock(has_insider_buying=True, consecutive_buy_days=5, is_obv_rising=True),
            smart_money=False)
        self.assertEqual(base, with_signals)

    def test_insider_bonus_is_10(self):
        base = self._score(_base_stock())
        insider = self._score(_base_stock(has_insider_buying=True))
        self.assertAlmostEqual(insider - base, 10.0)

    def test_streak_with_obv_is_7(self):
        base = self._score(_base_stock())
        streak = self._score(_base_stock(consecutive_buy_days=3, is_obv_rising=True))
        self.assertAlmostEqual(streak - base, 7.0)

    def test_streak_only_needs_4_days(self):
        base = self._score(_base_stock())
        self.assertAlmostEqual(
            self._score(_base_stock(consecutive_buy_days=4, is_obv_rising=False)) - base, 4.0)
        # 3일 연속은 OBV 없이는 가점 없음
        self.assertAlmostEqual(
            self._score(_base_stock(consecutive_buy_days=3, is_obv_rising=False)) - base, 0.0)

    def test_combined_bonus_is_capped(self):
        base = self._score(_base_stock())
        stacked = self._score(
            _base_stock(has_insider_buying=True, consecutive_buy_days=5, is_obv_rising=True))
        # 10 + 7 = 17이지만 캡(12)까지만 반영되어야 한다
        self.assertAlmostEqual(stacked - base, SMART_MONEY_BONUS_CAP)

    def test_sentiment_gated_separately(self):
        """센티먼트/미시구조는 수급 플래그가 켜져 있어도 별도 플래그 없이는 미반영."""
        base = self._score(_base_stock())
        with_sentiment_data = self._score(
            _base_stock(discussion_traffic=2, net_buying=100.0))
        # net_buying 변화는 단일 종목 정규화에서 0점 처리되므로 순수하게 센티먼트 가점 여부만 남는다
        self.assertAlmostEqual(with_sentiment_data - base, 0.0)


if __name__ == "__main__":
    unittest.main()
