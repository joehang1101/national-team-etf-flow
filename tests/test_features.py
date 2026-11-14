from datetime import date, timedelta
from statistics import mean, stdev
import unittest

from features import (standardize, rolling_features, price_features, describe_state,
                      extract_features, flow_rate)


class FeatureTests(unittest.TestCase):
    def test_z_uses_sample_deviation_and_does_not_require_normality(self):
        baseline = list(range(60))
        r = standardize(100, baseline)
        self.assertAlmostEqual(r['z'], (100-mean(baseline))/stdev(baseline))
        self.assertEqual(r['percentile'], 1)
        self.assertIsNone(standardize(1, [0]*60)['z'])
        self.assertIsNone(standardize(1, [0]*59)['z'])
        self.assertIsNone(standardize(1, [None]+[0]*59)['z'])

    def test_current_day_and_target_window_are_excluded_from_baseline(self):
        values = [i % 7 / 100 for i in range(150)]
        signatures = ['same']*150
        r = rolling_features(values, signatures, 149)
        baseline = [sum(values[j-4:j+1]) for j in range(85,145)]
        self.assertAlmostEqual(r['flow_5d_z'], (sum(values[145:150])-mean(baseline))/stdev(baseline))
        altered = values[:]
        altered[-1] = 999
        a = rolling_features(altered, signatures, 149)
        self.assertEqual(a['rate_mean_60'], r['rate_mean_60'])
        self.assertEqual(a['rate_sigma_60'], r['rate_sigma_60'])
        self.assertGreater(a['flow_1d_z'], r['flow_1d_z'])

    def test_gaps_and_membership_changes_do_not_compress_history(self):
        values = [i % 7 / 100 for i in range(150)]
        values[-5] = None
        r = rolling_features(values, ['a']*150, 149)
        self.assertIsNone(r['flow_1d_z'])
        self.assertIsNone(r['flow_5d_rate'])
        self.assertLess(r['flow_1d_samples'], 60)
        self.assertEqual(r['inflow_streak'], 2)  # final values: 0, .01, .02
        values = [.01]*149+[.02]
        changed = ['a']*149+['b']
        r = rolling_features(values, changed, 149)
        self.assertIsNone(r['flow_5d_rate'])
        self.assertIsNone(r['flow_1d_z'])
        self.assertEqual(r['inflow_streak'], 1)

    def test_missing_or_zero_flow_breaks_streak(self):
        self.assertIsNone(rolling_features([.1,None], ['a']*2, 1)['inflow_streak'])
        r = rolling_features([.1,0], ['a']*2, 1)
        self.assertEqual((r['inflow_streak'], r['outflow_streak']), (0,0))

    def test_price_windows_no_forward_fill_and_correct_return_horizon(self):
        prices = [100+i for i in range(260)]
        r = price_features(prices, 259)
        self.assertAlmostEqual(r['return_3d'], 359/356-1)
        self.assertEqual(r['drawdown_60'], 0)
        self.assertAlmostEqual(r['distance_ma250'], 359/mean(prices[-250:])-1)
        prices[-2] = None
        r = price_features(prices, 259)
        self.assertIsNone(r['return_1d'])
        self.assertIsNone(r['volatility_20_annualized'])
        self.assertIsNone(r['distance_ma250'])

    def test_state_requires_actual_flow_sign_and_breadth_not_just_z(self):
        r = dict(expected=3, covered=3, flow_1d_rate=.01, flow_1d_z=2.1,
                 return_1d=-.01, inflow_share=2/3, outflow_count=1)
        self.assertEqual(describe_state(r)[0], '下跌中的广泛流入')
        r['flow_1d_rate'] = -.01
        self.assertEqual(describe_state(r)[0], '资金强度偏离常态')
        r.update(flow_1d_z=-2.1, return_1d=.01, inflow_share=1/3, outflow_count=2)
        self.assertEqual(describe_state(r)[0], '上涨中的广泛流出')
        r.update(inflow_share=0,outflow_count=1)  # zeros are not net redemptions
        self.assertEqual(describe_state(r)[0], '资金强度偏离常态')
        r['covered'] = 2
        self.assertEqual(describe_state(r)[0], '数据不足')

    def test_source_and_future_split_evidence_gate_rate(self):
        r = dict(source_verified=True,f=10,previous_share=100,previous_nav=2)
        self.assertEqual(flow_rate(r,'20261113'), .05)
        r['event_published_at'] = '2026-11-14'
        self.assertIsNone(flow_rate(r,'20261113'))
        r.pop('event_published_at'); r['source_verified'] = False
        self.assertIsNone(flow_rate(r,'20261113'))

    def test_pool_excludes_controls_normalizes_by_assets_and_keeps_gross_structure(self):
        day='20261113'
        entries=[dict(code='a',name='a',category='沪深300',role='candidate'),
                 dict(code='b',name='b',category='创业板',role='candidate')]
        entries += [dict(code=str(i),name='proxy',category='A500',role='control_candidate',
                         control_status='reviewed',control_source_url='https://issuer/report',
                         control_evidence_available_at='2026-11-14') for i in range(3)]
        rows=[dict(d=day,c=e['code'],f=10 if e['code']=='a' else -5,
                   previous_share=100,previous_nav=1,source_verified=True) for e in entries]
        p=dict(dates=['2026-11-13'], data_asof=day,daily_records=rows)
        s=extract_features(p,entries)['latest']
        self.assertEqual(s['expected'], 2)
        self.assertEqual(s['net_flow_cny'], 5)
        self.assertEqual(s['flow_1d_rate'], .025)
        self.assertEqual(s['inflow_share'], .5)
        self.assertEqual(s['inflow_large_share'], 1)
        self.assertEqual(s['outflow_small_share'], 1)
        self.assertIsNone(s['control_rate'])
        for e in entries[2:]: e['control_evidence_available_at']='2026-11-13'
        s=extract_features(p,entries)['latest']
        self.assertEqual(s['control_rate'], -.05)
        self.assertEqual(s['control_count'], 3)
        rows[0]['source_verified'] = False
        s=extract_features(p,entries)['latest']
        self.assertIsNone(s['flow_1d_rate'])
        self.assertIsNone(s['inflow_gross_cny'])
        self.assertEqual(s['state'], '数据不足')

    def test_appending_future_does_not_change_past_features(self):
        entries=[dict(code='a',name='a',category='沪深300',role='candidate')]
        dates=[(date(2026,1,1)+timedelta(days=i)).strftime('%Y%m%d') for i in range(105)]
        rows=[dict(d=d,c='a',f=i%7-2,previous_share=100,previous_nav=1,source_verified=True) for i,d in enumerate(dates)]
        p=dict(dates=dates[:-1],data_asof=dates[-2],daily_records=rows[:-1])
        previous=extract_features(p,entries)['records']
        p.update(dates=dates,data_asof=dates[-1],daily_records=rows)
        future=extract_features(p,entries)['records']
        self.assertEqual(previous, future[:-2])

    def test_absent_whole_session_is_not_silently_skipped(self):
        entries=[dict(code='a',name='a',category='沪深300',role='candidate')]
        p=dict(dates=['20261111','20261113'],feature_trade_days=['20261111','20261112','20261113'],
               data_asof='20261113',daily_records=[dict(d=d,c='a',f=10,previous_share=100,
               previous_nav=1,source_verified=True) for d in ['20261111','20261113']])
        f=extract_features(p,entries)
        self.assertEqual(len(f['pool_history']),3)
        self.assertIsNone(f['pool_history'][1]['flow_1d_rate'])
        self.assertEqual(f['latest']['inflow_streak'],1)

    def test_cached_windows_match_uncached_logic(self):
        values=[(i%7)/100 if i!=75 else None for i in range(110)]
        signatures=['a']*90+['b']*20
        cache={}
        for i in range(len(values)):
            self.assertEqual(rolling_features(values,signatures,i),rolling_features(values,signatures,i,cache))


if __name__ == '__main__':
    unittest.main()
