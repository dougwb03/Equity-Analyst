import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("factor_check", Path(__file__).with_name("factor_check.py"))
factor_check = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(factor_check)


class FactorCheckTests(unittest.TestCase):
    def rows(self):
        return [
            {"new_company_cik": "0000000001", "new_company_name": "One", "symbol": {"value": "ONE"}, "factor_values": {"market_cap": {"value": 10}, "price_return": {"value": -0.2}, "insider_ownership_pct": {"value": 1}}},
            {"new_company_cik": "0000000002", "new_company_name": "Two", "symbol": {"value": "TWO"}, "factor_values": {"market_cap": {"value": 20}, "price_return": {"value": 0.2}, "insider_ownership_pct": {"value": 2}}},
        ]

    def plan(self):
        return {"holding_period_calendar_days": 90, "factors": [{"name": "market_cap", "direction": "lower", "weight": 40}, {"name": "price_return", "direction": "lower", "weight": 35}]}

    def test_declared_lower_direction_sets_high_priority_group(self):
        outcomes = {"0000000001": {"entry_date":"2025-01-02","entry_close":10,"exit_date":"2025-04-02","exit_close":11}, "0000000002": {"entry_date":"2025-01-02","entry_close":10,"exit_date":"2025-04-02","exit_close":9}}
        result = factor_check.compare(self.rows(), outcomes, self.plan())
        market = result["comparisons"][0]
        self.assertEqual(market["high_priority"][0]["cik"], "0000000001")
        self.assertEqual(market["high_priority_median_return"], 0.1)

    def test_missing_outcome_is_excluded_not_estimated(self):
        result = factor_check.compare(self.rows(), {"0000000001": {"entry_date":"2025-01-02","entry_close":10,"exit_date":"2025-04-02","exit_close":11}}, self.plan())
        self.assertEqual(result["excluded"], [{"cik": "0000000002", "reason": "missing entry or exit evidence"}])

    def test_zero_weight_factor_makes_no_comparison(self):
        plan = self.plan(); plan["factors"][0]["weight"] = 0
        result = factor_check.compare(self.rows(), {"0000000001": {"entry_date":"2025-01-02","entry_close":10,"exit_date":"2025-04-02","exit_close":11}, "0000000002": {"entry_date":"2025-01-02","entry_close":10,"exit_date":"2025-04-02","exit_close":9}}, plan)
        self.assertEqual([item["factor"] for item in result["comparisons"]], ["price_return"])


if __name__ == "__main__":
    unittest.main()
