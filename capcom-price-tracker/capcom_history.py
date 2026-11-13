"""Load reviewed publisher histories and preserve unknown price/edition periods.

The module name and default CAPCOM arguments remain compatible with older jobs.
"""

import csv
import hashlib
import json
import datetime as dt
from pathlib import Path

from price_history import reconstruct, JST, UTC


def export_prefix(publisher, folder):
    return '' if publisher == 'CAPCOM' else folder.name + '_'


def load_details(folder: Path, publisher='CAPCOM'):
    manifest = folder / "review.csv"
    if not manifest.exists():
        return [], {}, {}
    with manifest.open(encoding="utf-8-sig", newline="") as f:
        entries = list(csv.DictReader(f))
    plan_path = folder / 'reviewed_periods.json'
    plans = json.loads(plan_path.read_text())['games'] if plan_path.exists() else {}
    output, seen, metadata, intervals, unknown = [], set(), {}, [], []
    for entry in entries:
        if entry["status"] not in ('approved', 'approved_partial'):
            continue
        aid = int(entry["appid"])
        if aid in seen or entry["publisher"] != publisher:
            raise ValueError("Duplicate AppID or wrong publisher in historical approval manifest")
        seen.add(aid)
        source = folder / "sources" / f"{aid}.csv"
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry["source_sha256"]:
            raise ValueError(f"History source changed after review: {aid}")
        if plans and plans.get(str(aid), {}).get('source_sha256') != entry['source_sha256']:
            raise ValueError(f'Price-period plan does not match source manifest: {aid}')
        days, priced, meta = reconstruct(source, entry, entry["list_price_usd"],
                                    coverage_start_utc=entry["coverage_start_utc"] or None,
                                    zero_price_is_unknown=True,
                                    reviewed_periods=plans.get(str(aid), {}).get('periods'))
        if len(days) != int(entry["imported_days"]) or meta["sale_days"] != int(entry["sale_days"]):
            raise ValueError(f"History coverage differs from approval manifest: {aid}")
        output.extend(days)
        metadata[str(aid)] = meta
        intervals.extend(dict(appid=str(aid), game_name=entry['game_name'], **r) for r in priced)
        unknown.extend(dict(appid=str(aid), game_name=entry['game_name'], **r)
                       for r in meta['unknown_intervals'])
    if plans:
        from collector import atomic_csv
        exports = folder.parents[1] / 'powerbi'
        prefix = export_prefix(publisher, folder)
        atomic_csv(exports / (prefix + 'historical_price_intervals.csv'),
                   ['appid', 'game_name', 'start_utc', 'end_utc_exclusive', 'start_jst',
                    'end_jst_exclusive', 'list_price_usd', 'sale_price_usd', 'discount_percent', 'is_on_sale'], intervals)
        atomic_csv(exports / (prefix + 'historical_unknown_intervals.csv'),
                   ['appid', 'game_name', 'start_utc', 'end_utc_exclusive', 'reason'], unknown)
    return output, plans, metadata


def load_history(folder: Path) -> list[dict]:
    return load_details(folder)[0]


def publisher_history(games, live, folder, stops, plans, metadata, collection_records=(), *, publisher='CAPCOM'):
    """Use the entire reviewed scope in the denominator, including missing games.

    Historical full-game editions can change by AppID. A discounted interval
    proves a sale day even with gaps. A historical no-sale day needs complete
    coverage of its eligible part; a live observation follows the sampling rule.
    """
    if not plans:
        return [], [], []
    plans = {aid: dict(plan) for aid, plan in plans.items()}
    metadata = dict(metadata)
    # Newly discovered titles do not yet have a historical source manifest.
    # Preserve their automatic first-observation eligibility in future rebuilds.
    for row in live:
        aid = str(row['appid'])
        if row['publisher'] == publisher and aid not in plans:
            observed = min(r['first_observed_utc'] for r in live
                           if r['publisher'] == publisher and str(r['appid']) == aid)
            plans[aid] = dict(game_name=row['game_name'], eligible_start_utc=observed,
                              first_source_event_utc=observed)
            metadata[aid] = dict(unknown_intervals=[])
    # Failed collections still define expected availability. In particular, a
    # newly catalogued game with no valid price must not vanish from this scope.
    records = [r for r in collection_records if r['publisher'] == publisher]
    for record in records:
        observed = dt.datetime.combine(dt.date.fromisoformat(record['date_jst']), dt.time(), JST).astimezone(UTC).isoformat()
        for aid in filter(None, record['missing_appids'].split(';')):
            if aid not in plans:
                plans[aid] = dict(game_name=f'AppID {aid}', eligible_start_utc=observed,
                                 first_source_event_utc=observed)
                metadata[aid] = dict(unknown_intervals=[])
            else:
                plans[aid]['eligible_start_utc'] = min(plans[aid]['eligible_start_utc'], observed)
    evidence = {(r['date_jst'], str(r['appid'])): r for r in games if r['publisher'] == publisher}
    live_keys = {(r['date_jst'], str(r['appid'])) for r in live if r['publisher'] == publisher}
    ends = [r['date_jst'] for r in games if r['publisher'] == publisher] + [r['date_jst'] for r in records]
    if not ends:
        return [], [], []
    day = min(dt.datetime.fromisoformat(p['eligible_start_utc']).astimezone(JST).date()
              for p in plans.values())
    # Before the first raw price record there is no source-supported series.
    source_start = min(dt.datetime.fromisoformat(p['first_source_event_utc']).astimezone(JST).date()
                       for p in plans.values())
    day = max(day, source_start)
    end = dt.date.fromisoformat(max(ends))
    publishers, coverage, gaps = [], [], []
    opened = {}
    while day <= end:
        date = day.isoformat()
        midnight = dt.datetime.combine(day, dt.time(), JST).astimezone(UTC)
        next_midnight = midnight + dt.timedelta(days=1)
        eligible, known, missing, reasons, samples = [], [], [], {}, []
        for aid, plan in plans.items():
            start = dt.datetime.fromisoformat(plan['eligible_start_utc'])
            if start >= next_midnight or date >= stops.get((publisher, aid), '9999-12-31'):
                continue
            row = evidence.get((date, aid))
            required = (next_midnight - max(start, midnight)).total_seconds()
            for period in plan.get('unavailable_periods', []):
                a = max(start, midnight, dt.datetime.fromisoformat(period['start_utc']))
                b = min(next_midnight, dt.datetime.fromisoformat(period['end_utc_exclusive']))
                required -= max(0, (b - a).total_seconds())
            if required <= 0:
                if aid in opened:
                    gaps.append(opened.pop(aid))
                continue
            eligible.append(aid)
            complete = row is not None and ((date, aid) in live_keys or int(row['is_on_sale_any']) or
                       float(row.get('coverage_seconds', 0)) >= required)
            if complete:
                known.append(row)
                samples.append(int(row['sample_count']))
                if aid in opened:
                    gaps.append(opened.pop(aid))
                continue
            missing.append(aid)
            reason = 'no_price_coverage'
            if row:
                reason = 'partial_day_without_sale_evidence'
            else:
                for gap in metadata[aid]['unknown_intervals']:
                    if (dt.datetime.fromisoformat(gap['start_utc']) < next_midnight and
                            dt.datetime.fromisoformat(gap['end_utc_exclusive']) > midnight):
                        reason = gap['reason']
                        break
            reasons[aid] = reason
            old = opened.get(aid)
            if old and old['reason'] == reason:
                old['end_date_jst'] = date
                old['days'] += 1
            else:
                if old:
                    gaps.append(old)
                opened[aid] = dict(appid=aid, game_name=plan['game_name'], start_date_jst=date,
                                   end_date_jst=date, days=1, reason=reason)
        if eligible:
            count, sale = len(eligible), sum(int(r['is_on_sale_any']) for r in known)
            complete = not missing
            publishers.append(dict(date_jst=date, publisher=publisher, games_monitored=count,
                              games_on_sale=sale if complete else '',
                              sale_share=f'{sale / count:.6f}' if complete else '',
                              sample_count_min=min(samples, default=0) if complete else 0,
                              sample_count_max=max(samples, default=0)))
            coverage.append(dict(date_jst=date, publisher=publisher, expected_games=count,
                            observed_games=len(known), missing_games=len(missing), is_complete=int(complete),
                            missing_appids=';'.join(sorted(missing, key=int)),
                            status='complete' if complete else 'incomplete'))
        day += dt.timedelta(days=1)
    gaps.extend(opened.values())
    return publishers, coverage, sorted(gaps, key=lambda r: (int(r['appid']), r['start_date_jst']))
