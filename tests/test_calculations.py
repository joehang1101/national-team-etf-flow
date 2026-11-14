import unittest
from datetime import datetime
from unittest.mock import patch

from calculations import calculate_daily
import fetch as refresh


class CalculationTests(unittest.TestCase):
    targets = [('510300', 'test', 'sh')]
    days = ['20261110', '20261111', '20261112']

    def calculate(self, shares, nav):
        shares = {day: {'510300': value} for day, value in shares.items()}
        return calculate_daily(self.targets, shares, {'510300': nav}, sorted(shares), self.days)

    def test_subscription_units_and_real_zero(self):
        rows = self.calculate(dict(zip(self.days, [100_000_000, 120_000_000, 120_000_000])),
                              dict(zip(self.days, [1, 1.5, 1.6])))
        self.assertIsNone(rows[0]['f'])
        self.assertEqual(rows[1]['f'] / 1e8, .3)
        self.assertEqual(rows[2]['f'], 0)

    def test_redemption(self):
        rows = self.calculate(dict(zip(self.days[:2], [120, 100])), {self.days[1]: 1.5})
        self.assertEqual(rows[-1]['f'], -30)

    def test_missing_day_is_not_attributed_to_next_day(self):
        rows = self.calculate({self.days[0]: 100, self.days[2]: 130}, {self.days[2]: 2})
        self.assertIsNone(rows[-1]['f'])
        self.assertIsNone(rows[-1]['delta'])

    def test_missing_nav_is_not_forward_filled(self):
        rows = self.calculate(dict(zip(self.days, [100, 120, 130])), {self.days[0]: 1})
        self.assertIsNone(rows[-1]['f'])
        self.assertEqual(rows[-1]['status'], 'missing_nav')

    def test_possible_split_needs_review(self):
        rows = self.calculate(dict(zip(self.days[:2], [100, 50])), dict(zip(self.days[:2], [1, 2])))
        self.assertIsNone(rows[-1]['f'])
        self.assertIsNone(rows[-1]['delta'])
        self.assertEqual(rows[-1]['status'], 'possible_split')

    def test_nonfinite_inputs_are_missing(self):
        rows = self.calculate(dict(zip(self.days, [100, float('nan'), 130])), {self.days[-1]: float('inf')})
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[-1]['f'])

    def test_holiday_is_not_a_missing_session(self):
        shares = {'20260930': {'510300': 100}, '20261009': {'510300': 120}}
        rows = calculate_daily(self.targets, shares, {'510300': {'20261009': 2}}, sorted(shares), sorted(shares))
        self.assertEqual(rows[-1]['f'], 40)


class CalendarTests(unittest.TestCase):
    def expected(self, stamp, calendar):
        with patch.object(refresh, 'now_bj', return_value=datetime.fromisoformat(stamp).replace(tzinfo=refresh.BEIJING)):
            return refresh.expected_latest_cut(set(calendar))

    def test_after_close_targets_today(self):
        self.assertEqual(self.expected('2026-11-13T16:00', ['20261112', '20261113']), 20261113)

    def test_before_close_targets_previous_day(self):
        self.assertEqual(self.expected('2026-11-13T09:00', ['20261112', '20261113']), 20261112)

    def test_weekend(self):
        self.assertEqual(self.expected('2026-11-14T11:00', ['20261112', '20261113']), 20261113)

    def test_holiday(self):
        self.assertEqual(self.expected('2026-10-05T17:00', ['20260930', '20261009']), 20260930)

    def test_cold_and_warm_windows_use_calendar_days(self):
        with patch.object(refresh, 'now_bj', return_value=datetime(2026, 11, 14, tzinfo=refresh.BEIJING)):
            self.assertEqual(refresh.natural_dates(14)[0], '20261102')
            self.assertEqual(refresh.incremental_dates({}, 14), refresh.natural_dates(14))


if __name__ == '__main__':
    unittest.main()
