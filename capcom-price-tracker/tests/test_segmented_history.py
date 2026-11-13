import csv
import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import collector as c
from capcom_history import publisher_history
from price_history import reconstruct


class SegmentedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.item = dict(publisher='CAPCOM', appid='1', game_name='One', package_id='10', tracked_edition='Base')

    def tearDown(self):
        self.tmp.cleanup()

    def reconstruct(self, events, periods):
        path = self.root / 'source.csv'
        c.atomic_csv(path, ['DateTime', 'Final price'],
                     [dict(DateTime=t, **{'Final price': p}) for t, p in events])
        return reconstruct(path, self.item, '19.99', zero_price_is_unknown=True, reviewed_periods=periods)

    def test_price_cut_is_not_a_permanent_sale_and_subcent_rounding_is_binary(self):
        days, intervals, meta = self.reconstruct([
            ('2025-01-01 15:00:00', '59.99'),
            ('2025-01-02 15:00:00', '39.99'),
            ('2025-01-03 15:00:00', '9.99'),
            ('2025-01-04 15:00:00', '39.99'),
        ], [dict(start_utc='2025-01-01T15:00:00+00:00', list_price_usd='59.99'),
            dict(start_utc='2025-01-02T15:00:00+00:00', list_price_usd='39.99')])
        self.assertEqual([d['is_on_sale_any'] for d in days], [0, 0, 1])
        self.assertEqual([d['max_discount_percent'] for d in days], [0, 0, 75])
        self.assertEqual([d['coverage_seconds'] for d in days], [86400, 86400, 86400])

    def test_unknown_chapter_and_transient_high_price_do_not_become_sale_or_zero(self):
        days, _, meta = self.reconstruct([
            ('2025-01-01 15:00:00', '5.99'),
            ('2025-01-02 15:00:00', '19.99'),
            ('2025-01-03 03:00:00', '79.99'),
            ('2025-01-03 04:00:00', '19.99'),
            ('2025-01-03 15:00:00', '4.99'),
            ('2025-01-04 15:00:00', '19.99'),
        ], [dict(start_utc='2025-01-01T15:00:00+00:00', list_price_usd=None),
            dict(start_utc='2025-01-02T15:00:00+00:00', list_price_usd='19.99')])
        self.assertEqual([(d['date_jst'], d['is_on_sale_any']) for d in days], [('2025-01-04', 1)])
        self.assertEqual({d['reason'] for d in meta['unknown_intervals']},
                         {'edition_or_opening_baseline_unknown', 'price_above_reviewed_baseline'})

    def test_missing_game_stays_in_denominator_preorder_enters_future_title_waits(self):
        plans = {a: dict(game_name=a, eligible_start_utc=start, first_source_event_utc=start)
                 for a, start in [('1', '2025-01-01T15:00:00+00:00'),
                                  ('2', '2025-01-02T03:00:00+00:00'),
                                  ('3', '2025-01-10T00:00:00+00:00')]}
        row = dict(self.item, date_jst='2025-01-02', is_on_sale_any=1, sample_count=0, coverage_seconds=10)
        pubs, coverage, gaps = publisher_history([row], [], self.root, {}, plans,
                                                  {a: dict(unknown_intervals=[]) for a in plans})
        self.assertEqual((pubs[0]['games_monitored'], pubs[0]['sale_share']), (2, ''))
        self.assertEqual(coverage[0]['missing_appids'], '2')
        self.assertEqual(gaps[0]['appid'], '2')

    def test_partial_negative_day_is_unknown_until_live_observation(self):
        plan = {'1': dict(game_name='One', eligible_start_utc='2025-01-01T15:00:00+00:00',
                          first_source_event_utc='2025-01-01T15:00:00+00:00')}
        row = dict(self.item, date_jst='2025-01-02', is_on_sale_any=0, sample_count=0,
                   coverage_seconds=3600, first_observed_utc='2025-01-02T01:00:00+00:00')
        args = (self.root, {}, plan, {'1': dict(unknown_intervals=[])})
        self.assertEqual(publisher_history([row], [], *args)[0][0]['sale_share'], '')
        self.assertEqual(publisher_history([row], [row], *args)[0][0]['sale_share'], '0.000000')

    def test_confirmed_temporary_unavailability_exits_denominator_then_returns(self):
        plan = {a: dict(game_name=a, eligible_start_utc='2025-01-01T15:00:00+00:00',
                         first_source_event_utc='2025-01-01T15:00:00+00:00') for a in ('1', '2')}
        plan['2']['unavailable_periods'] = [dict(start_utc='2025-01-01T15:00:00+00:00',
                                               end_utc_exclusive='2025-01-02T15:00:00+00:00')]
        rows = [dict(self.item, date_jst=date, is_on_sale_any=1, sample_count=0, coverage_seconds=86400)
                for date in ('2025-01-02', '2025-01-03')]
        pubs, _, _ = publisher_history(rows, [], self.root, {}, plan,
                                        {a: dict(unknown_intervals=[]) for a in plan})
        self.assertEqual([(p['games_monitored'], p['sale_share']) for p in pubs], [(1, '1.000000'), (2, '')])

    def test_future_auto_discovered_title_is_preserved_without_a_history_manifest(self):
        plan = {'1': dict(game_name='One', eligible_start_utc='2025-01-01T15:00:00+00:00',
                          first_source_event_utc='2025-01-01T15:00:00+00:00')}
        rows = [dict(self.item, appid=a, date_jst='2025-01-02', is_on_sale_any=1, sample_count=1,
                     first_observed_utc='2025-01-02T01:00:00+00:00') for a in ('1', '2')]
        pubs, coverage, _ = publisher_history(rows, rows, self.root, {}, plan,
                                               {'1': dict(unknown_intervals=[])})
        self.assertEqual((pubs[0]['games_monitored'], pubs[0]['sale_share']), (2, '1.000000'))
        self.assertEqual(coverage[0]['is_complete'], 1)

    def test_all_failed_day_after_last_game_record_is_retained(self):
        plan = {'1': dict(game_name='One', eligible_start_utc='2025-01-01T15:00:00+00:00',
                          first_source_event_utc='2025-01-01T15:00:00+00:00')}
        row = dict(self.item, date_jst='2025-01-02', is_on_sale_any=0, sample_count=1,
                   first_observed_utc='2025-01-02T01:00:00+00:00')
        records = [dict(date_jst='2025-01-03', publisher='CAPCOM', missing_appids='1;2')]
        pubs, coverage, gaps = publisher_history([row], [row], self.root, {}, plan,
                                                   {'1': dict(unknown_intervals=[])}, records)
        self.assertEqual((pubs[-1]['date_jst'], pubs[-1]['games_monitored'], pubs[-1]['sale_share']),
                         ('2025-01-03', 2, ''))
        self.assertEqual(coverage[-1]['missing_appids'], '1;2')
        self.assertNotIn('2', plan)  # Scope construction must not mutate source plans.

    def test_failed_new_game_does_not_shrink_same_day_publisher_denominator(self):
        plan = {'1': dict(game_name='One', eligible_start_utc='2025-01-01T15:00:00+00:00',
                          first_source_event_utc='2025-01-01T15:00:00+00:00')}
        row = dict(self.item, date_jst='2025-01-02', is_on_sale_any=1, sample_count=1,
                   first_observed_utc='2025-01-02T01:00:00+00:00')
        records = [dict(date_jst='2025-01-02', publisher='CAPCOM', missing_appids='2')]
        pubs, coverage, _ = publisher_history([row], [row], self.root, {}, plan,
                                              {'1': dict(unknown_intervals=[])}, records)
        self.assertEqual((pubs[-1]['games_monitored'], pubs[-1]['sale_share']), (2, ''))
        self.assertEqual(coverage[-1]['observed_games'], 1)


if __name__ == '__main__':
    unittest.main()
