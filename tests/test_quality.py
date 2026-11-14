import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from calculations import calculate_daily
from provenance import migrate_cache, merge_observation, repair_dates
from fetch import date_batches, sync_day_of
from attribution import known_anchors, anchor_intervals, attribute, holding_bounds
from universe import members_on
from exports import write_csv


class ProvenanceTests(unittest.TestCase):
    def test_migration_preserves_values_without_inventing_official_source(self):
        cache={'shares':{'20200102':{'510300':123}}}
        migrate_cache(cache); migrate_cache(cache)
        self.assertEqual(cache['shares']['20200102']['510300'],123)
        self.assertEqual(cache['share_meta']['20200102']['510300']['source'],'legacy_unknown')

    def test_official_replaces_fallback_and_cannot_be_downgraded(self):
        c={'shares':{}}
        merge_observation(c,'20200102','510300',100,'fallback','one')
        merge_observation(c,'20200102','510300',110,'sse','two')
        self.assertFalse(merge_observation(c,'20200102','510300',120,'fallback','three'))
        self.assertEqual(c['shares']['20200102']['510300'],110)
        self.assertEqual(c['share_meta']['20200102']['510300']['replaces_source'],'fallback')
        merge_observation(c,'20200102','510300',110,'sse','four')
        self.assertEqual(c['share_meta']['20200102']['510300']['observed_at'],'two')

    def test_repair_old_fallback_outside_recent_window(self):
        c={'shares':{}}
        for day in ['20200102','20200103']:
            merge_observation(c,day,'510300',100,'fallback','one')
        self.assertIn('20200102',repair_dates(c,['20261113'],1))
        self.assertIn('20200103',repair_dates(c,['20261113'],1))
        merge_observation(c,'20200102','510300',100,'sse','two')
        self.assertNotIn('20200102',repair_dates(c,[],8))

    def test_mixed_old_repair_and_recent_dates_do_not_exceed_szse_range(self):
        batches=list(date_batches(['20200102','20200103','20261112','20261113']))
        self.assertEqual(batches,[['20200102','20200103'],['20261112','20261113']])


class SplitTests(unittest.TestCase):
    days=['20261112','20261113']
    def calc(self,after,nav,event=None,meta=None):
        return calculate_daily([('510300','test','sh')],{self.days[0]:{'510300':100},self.days[1]:{'510300':after}},
              {'510300':{self.days[0]:1,self.days[1]:nav}},self.days,self.days,meta,event)[-1]

    def test_small_reciprocal_adjustment_is_flagged_not_zeroed(self):
        r=self.calc(98,100/98)
        self.assertEqual(r['status'],'possible_split'); self.assertIsNone(r['f'])

    def test_confirmed_small_adjustment_preserves_concurrent_subscription(self):
        event=[{'date':'20261113','code':'510300','factor':1.002,'verified':True,
                'published_at':'2026-11-12','source_url':'https://example.com/notice'}]
        r=self.calc(102.2,1,event)
        self.assertAlmostEqual(r['f'],2)
        self.assertEqual(r['status'],'confirmed_split')

    def test_unverified_adjustment_is_not_treated_as_zero(self):
        r=self.calc(102,1,[{'date':'20261113','code':'510300','factor':1.02}])
        self.assertEqual(r['status'],'unverified_event'); self.assertIsNone(r['f'])

    def test_ordinary_flow_and_price_move_are_not_a_split(self):
        r=calculate_daily([('510230','test','sh')],{'20261112':{'510230':566808000},'20261113':{'510230':560808000}},
            {'510230':{'20261112':1.3533,'20261113':1.3676}},self.days,self.days)[-1]
        self.assertEqual(r['status'],'ok'); self.assertAlmostEqual(r['f'],-6000000*1.3676)

    def test_fallback_cannot_enter_research_calculation(self):
        meta={d:{'510300':{'source':'fallback'}} for d in self.days}
        r=self.calc(120,1,meta=meta)
        self.assertIsNone(r['f']); self.assertFalse(r['backtest_eligible'])


class SyncAndEvidenceTests(unittest.TestCase):
    def test_common_day_does_not_follow_single_exchange_ahead(self):
        s={'20261112':{'510300':1,'159919':1},'20261113':{'510300':1}}
        self.assertEqual(sync_day_of(s,sorted(s)),'20261112')

    def test_single_market_fallback_is_explicit(self):
        s={'20261113':{'510300':1}}
        self.assertEqual(sync_day_of(s,sorted(s)),'20261113')

    def test_future_disclosure_cannot_be_used_as_past_evidence(self):
        a={'code':'510300','report_date':'20251231','published_at':'2026-03-31','shares':100,'verified':True,'source_url':'https://example.com'}
        self.assertEqual(known_anchors([a],'510300','20260101'),[])
        self.assertEqual(known_anchors([a],'510300','20260401'),[a])

    def test_interval_has_no_invented_daily_path(self):
        base={'code':'510300','holder':'test','verified':True,'source_url':'https://example.com','share_basis':'same'}
        a=dict(base,report_date='20250630',published_at='2025-08-30',shares=100)
        b=dict(base,report_date='20251231',published_at='2026-03-31',shares=120)
        r=anchor_intervals([a,b])[0]
        self.assertEqual(r['share_change'],20); self.assertFalse(r['daily_path_identifiable'])
        self.assertEqual(r['available_at'],'2026-03-31')
        b['share_basis']='changed'
        self.assertIsNone(anchor_intervals([a,b])[0]['share_change'])

    def test_capacity_bounds_are_conditional_and_never_use_future_disclosure(self):
        anchor={'code':'510300','holder':'test','report_date':'20251231','published_at':'2026-03-31',
                'shares':100,'verified':True,'source_url':'https://example.com','share_basis':'same'}
        day={'d':'20260101','c':'510300','s':70,'share_source':'sse'}
        self.assertEqual(holding_bounds([day],[anchor]),[])
        day['d']='20260401'
        r=holding_bounds([day],[anchor])[0]
        self.assertEqual((r['holding_min'],r['holding_max']),(0,70))
        self.assertEqual(r['minimum_holding_reduction'],30)
        self.assertTrue(r['not_etf_redemptions'])
        self.assertIn('unrecorded splits',r['assumption'])

    def test_universe_effective_and_knowledge_dates(self):
        e={'code':'1','effective_from':'2025-01-01','effective_to':'2025-12-31','known_from':'2025-02-01'}
        self.assertEqual(members_on('20250115',entries=[e]),[])
        self.assertEqual(members_on('20250301',entries=[e]),[e])
        self.assertEqual(members_on('20260101',entries=[e]),[])

    def test_csv_missing_is_empty_and_real_zero_is_zero(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'data.csv'
            write_csv(p,[{'value':None},{'value':0}],['value'])
            rows=list(csv.DictReader(io.StringIO(p.read_text(encoding='utf-8-sig'))))
            self.assertEqual(rows,[{'value':''},{'value':'0'}])

    def test_unreviewed_controls_never_produce_identity_probability(self):
        daily=[{'d':'20261113','c':'1','f':100,'previous_share':100,'previous_nav':1,'backtest_eligible':True}]
        entries=[{'code':'1','role':'candidate'},{'code':'2','role':'control_candidate','control_status':'unverified'}]
        r=attribute(daily,entries,[])[0]
        self.assertEqual(r['label'],'证据不足'); self.assertIsNone(r['identity_probability'])

    def test_model_uses_prior_samples_only_and_caps_evidence_without_anchor(self):
        from datetime import date,timedelta
        entries=[{'code':'1','role':'candidate'}]+[{'code':str(i),'role':'control_candidate','control_status':'reviewed',
                   'known_from':'20200101','control_source_url':'https://example.com'} for i in (2,3,4)]
        rows=[]
        for i in range(25):
            day=(date(2020,1,1)+timedelta(days=i)).strftime('%Y%m%d'); x=(i%7-3)*.001
            for code in ('1','2','3','4'):
                rate=(x*2+.0001*(i%3)) if code=='1' else x
                if i==24 and code=='1': rate=.5
                rows.append({'d':day,'c':code,'f':rate*100,'previous_share':100,'previous_nav':1,'backtest_eligible':True})
        result=attribute(rows,entries,[])
        self.assertEqual(result[-1]['training_count'],24)
        self.assertGreater(result[-1]['residual_rate'],.4)
        self.assertEqual(result[-1]['evidence_grade'],'低')
        self.assertIsNone(result[-1]['identity_probability'])


if __name__=='__main__': unittest.main()
