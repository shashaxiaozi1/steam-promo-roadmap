import csv
import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).parents[1]))
import collector as c
from capcom_history import publisher_history, load_details, export_prefix
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

    def test_other_publishers_cannot_supply_missing_konami_price_or_retire_its_games(self):
        plan = {a: dict(game_name=a, eligible_start_utc='2025-01-01T15:00:00+00:00',
                         first_source_event_utc='2025-01-01T15:00:00+00:00') for a in ('1', '2')}
        rows = [dict(self.item, publisher=p, appid=a, date_jst='2025-01-02',
                     is_on_sale_any=1, sample_count=1, first_observed_utc='2025-01-02T01:00:00+00:00')
                for p, a in [('KONAMI', '1'), ('CAPCOM', '2')]]
        args = (rows, rows, self.root, {('CAPCOM', '1'): '2025-01-01'}, plan,
                {a: dict(unknown_intervals=[]) for a in plan})
        pubs, coverage, _ = publisher_history(*args, publisher='KONAMI')
        self.assertEqual((pubs[0]['publisher'], pubs[0]['games_monitored'], pubs[0]['sale_share']),
                         ('KONAMI', 2, ''))
        self.assertEqual(coverage[0]['missing_appids'], '2')

    def test_standalone_opening_does_not_inherit_collection_unknown_hours(self):
        path = self.root / 'source.csv'
        c.atomic_csv(path, ['DateTime', 'Final price'], [
            dict(DateTime='2025-01-01 00:00:00', **{'Final price': '59.99'}),
            dict(DateTime='2025-01-02 07:00:00', **{'Final price': '19.99'}),
            dict(DateTime='2025-01-03 15:00:00', **{'Final price': '19.99'})])
        start = '2025-01-02T07:00:00+00:00'
        item = dict(self.item, publisher='KONAMI')
        days, _, meta = reconstruct(path, item, '19.99', coverage_start_utc=start,
                                   reviewed_periods=[dict(start_utc=start, list_price_usd='19.99')])
        plan = {'1': dict(game_name='One', eligible_start_utc=start, first_source_event_utc=start)}
        pubs, _, gaps = publisher_history(days, [], self.root, {}, plan, {'1': meta}, publisher='KONAMI')
        self.assertEqual(pubs[0]['sale_share'], '0.000000')
        self.assertEqual(pubs[0]['games_monitored'], 1)
        self.assertEqual(gaps, [])

    def test_konami_exports_cannot_overwrite_legacy_capcom_audits(self):
        import hashlib
        import json
        folder = self.root / 'history' / 'konami'
        (folder / 'sources').mkdir(parents=True)
        source = folder / 'sources/1.csv'
        c.atomic_csv(source, ['DateTime', 'Final price'], [
            dict(DateTime='2025-01-01 15:00:00', **{'Final price': '19.99'}),
            dict(DateTime='2025-01-02 15:00:00', **{'Final price': '19.99'})])
        item = dict(self.item, publisher='KONAMI')
        periods = [dict(start_utc='2025-01-01T15:00:00+00:00', list_price_usd='19.99')]
        days, _, meta = reconstruct(source, item, '19.99', reviewed_periods=periods)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        entry = dict(item, status='approved', source_sha256=digest, list_price_usd='19.99',
                     coverage_start_utc=meta['coverage_start_utc'], imported_days=len(days), sale_days=0)
        c.atomic_csv(folder / 'review.csv', list(entry), [entry])
        (folder / 'reviewed_periods.json').write_text(json.dumps({'games': {'1': dict(source_sha256=digest, periods=periods)}}))
        exports = self.root / 'powerbi'
        exports.mkdir()
        legacy = exports / 'historical_price_intervals.csv'
        legacy.write_text('unchanged CAPCOM audit\n')
        loaded, _, _ = load_details(folder, 'KONAMI')
        self.assertEqual(len(loaded), 1)
        self.assertTrue((exports / 'konami_historical_price_intervals.csv').exists())
        self.assertEqual(legacy.read_text(), 'unchanged CAPCOM audit\n')
        self.assertEqual(export_prefix('CAPCOM', self.root / 'capcom'), '')
        with self.assertRaisesRegex(ValueError, 'wrong publisher'):
            load_details(folder, 'CAPCOM')

    def test_reviewed_spike_correction_preserves_original_source_and_rejects_mismatch(self):
        path = self.root / 'source.csv'
        c.atomic_csv(path, ['DateTime', 'Final price'], [
            dict(DateTime='2025-01-01 15:00:00', **{'Final price': '19.99'}),
            dict(DateTime='2025-01-02 03:00:00', **{'Final price': '29.99'}),
            dict(DateTime='2025-01-02 03:05:00', **{'Final price': '19.99'}),
            dict(DateTime='2025-01-02 15:00:00', **{'Final price': '19.99'})])
        original = path.read_bytes()
        override = dict(start_utc='2025-01-02T03:00:00+00:00', source_price_usd='29.99',
                        effective_price_usd='19.99', basis='Reviewed five-minute spike, same full price before and after')
        days, _, meta = reconstruct(path, self.item, '19.99',
            reviewed_periods=[dict(start_utc='2025-01-01T15:00:00+00:00', list_price_usd='19.99')],
            reviewed_price_overrides=[override])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual((len(days), days[0]['is_on_sale_any'], days[0]['coverage_seconds']), (1, 0, 86400))
        self.assertEqual(meta['reviewed_price_overrides'][0]['source_price_usd'], '29.99')
        with self.assertRaisesRegex(ValueError, 'original priced source event'):
            reconstruct(path, self.item, '39.99', reviewed_price_overrides=[dict(override, source_price_usd='28.99')])

    def test_explicit_one_cent_rule_keeps_original_price_and_does_not_hide_real_sale(self):
        path = self.root / 'one_cent.csv'
        c.atomic_csv(path, ['DateTime', 'Final price'], [
            dict(DateTime='2025-01-01 15:00:00', **{'Final price': '19.98'}),
            dict(DateTime='2025-01-10 15:00:00', **{'Final price': '9.99'}),
            dict(DateTime='2025-01-11 15:00:00', **{'Final price': '19.99'})])
        periods = [dict(start_utc='2025-01-01T15:00:00+00:00', list_price_usd='19.99')]
        original = path.read_bytes()
        days, _, meta = reconstruct(path, self.item, '19.99', reviewed_periods=periods,
                                   sale_price_tolerance_usd='0.01')
        self.assertEqual(len(days), 10)
        self.assertTrue(all(d['is_on_sale_any'] == d['max_discount_percent'] == 0 for d in days[:9]))
        self.assertTrue(all(d['lowest_sale_price_usd'] == '19.98' for d in days[:9]))
        self.assertEqual(days[-1]['is_on_sale_any'], 1)
        self.assertEqual(meta['sale_price_tolerance_usd'], '0.01')
        self.assertEqual(path.read_bytes(), original)
        with self.assertRaisesRegex(ValueError, 'too small'):
            reconstruct(path, self.item, '19.99', reviewed_periods=periods)
        with self.assertRaisesRegex(ValueError, 'cannot exceed'):
            reconstruct(path, self.item, '19.99', sale_price_tolerance_usd='0.02')

    def test_collection_only_transition_clips_last_standalone_day_and_later_denominator(self):
        path = self.root / 'source.csv'
        c.atomic_csv(path, ['DateTime', 'Final price'], [
            dict(DateTime='2025-01-01 15:00:00', **{'Final price': '19.99'}),
            dict(DateTime='2025-01-02 03:00:00', **{'Final price': '209.55'}),
            dict(DateTime='2025-01-04 15:00:00', **{'Final price': '59.99'})])
        start, end = '2025-01-01T15:00:00+00:00', '2025-01-02T03:00:00+00:00'
        item = dict(self.item, publisher='SEGA')
        days, _, meta = reconstruct(path, item, '19.99', coverage_end_utc=end,
                                   reviewed_periods=[dict(start_utc=start, list_price_usd='19.99')])
        self.assertEqual(len(days), 1)
        self.assertEqual(meta['coverage_end_utc_exclusive'], end)
        self.assertEqual(days[0]['coverage_seconds'], 43200)
        plan = {'1': dict(game_name='One', eligible_start_utc=start, eligible_end_utc=end, first_source_event_utc=start)}
        pubs, _, gaps = publisher_history(days, [], self.root, {}, plan, {'1': meta},
            [dict(date_jst='2025-01-04', publisher='SEGA', missing_appids='')], publisher='SEGA')
        self.assertEqual([(p['date_jst'], p['sale_share']) for p in pubs], [('2025-01-02', '0.000000')])
        self.assertEqual(gaps, [])
        with self.assertRaisesRegex(ValueError, 'coverage end'):
            reconstruct(path, item, '19.99', coverage_end_utc='2025-01-02T03:00:01+00:00',
                        reviewed_periods=[dict(start_utc=start, list_price_usd='19.99')])

    def test_publisher_review_supersedes_legacy_import_without_duplicate_fm26_days(self):
        import hashlib
        import json
        output = self.root / 'data'
        folder = output / 'history/sega'
        (folder / 'sources').mkdir(parents=True)
        source = folder / 'sources/1.csv'
        c.atomic_csv(source, ['DateTime', 'Final price'], [
            dict(DateTime='2025-01-01 15:00:00', **{'Final price': '19.99'}),
            dict(DateTime='2025-01-02 15:00:00', **{'Final price': '19.99'})])
        item = dict(self.item, publisher='SEGA')
        periods = [dict(start_utc='2025-01-01T15:00:00+00:00', list_price_usd='19.99')]
        days, _, meta = reconstruct(source, item, '19.99', reviewed_periods=periods)
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        entry = dict(item, status='approved', source_sha256=digest, list_price_usd='19.99',
                     coverage_start_utc=meta['coverage_start_utc'], imported_days=1, sale_days=0)
        c.atomic_csv(folder / 'review.csv', list(entry), [entry])
        plan = dict(source_sha256=digest, periods=periods, game_name='One',
                    eligible_start_utc=meta['coverage_start_utc'], first_source_event_utc=meta['coverage_start_utc'],
                    historical_package_id='10', historical_edition='Base')
        (folder / 'reviewed_periods.json').write_text(json.dumps({'games': {'1': plan}}))
        legacy = output / 'history/1/daily_games.csv'
        legacy.parent.mkdir()
        c.atomic_csv(legacy, c.GAME_FIELDS, [{k: days[0][k] for k in c.GAME_FIELDS}])
        original = legacy.read_bytes()
        games, pubs, _ = c.build_reports([], output, include_coverage=True)
        self.assertEqual((len(games), games[0]['package_id']), (1, '10'))
        self.assertEqual(pubs[0]['sale_share'], '0.000000')
        self.assertEqual(legacy.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
