"""Validate publisher denominators against expected active games, not only successes."""

import json

FIELDS = ["date_jst", "publisher", "expected_games", "observed_games", "missing_games",
          "is_complete", "missing_appids", "status"]


def build_coverage(live, output, report_start, stops, current_state=None):
    observed, expected = {}, {}
    for row in live:
        k = row["date_jst"], row["publisher"]
        observed.setdefault(k, set()).add(str(row["appid"]))
    for k, ids in observed.items():
        expected[k] = set(ids)
    for path in sorted((output / "collection_status").glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["run_id"] < report_start:
            continue
        k = record["date_jst"], record["publisher"]
        target = expected.setdefault(k, set())
        target.update(str(aid) for aid in record["expected_appids"]
                      if k[0] < stops.get((k[1], str(aid)), "9999-12-31"))
    states = [json.loads(p.read_text(encoding="utf-8")) for p in (output / "lifecycle").glob("*.json")]
    if current_state is not None:
        states.append(current_state)
    for state in states:
        for aid, entry in state.get("retired", {}).items():
            publisher = entry["catalog_entry"]["publisher"]
            first = entry.get("first_unavailable_date_jst", entry["effective_date_jst"])
            for (date, name), ids in expected.items():
                if name == publisher and first <= date < entry["effective_date_jst"]:
                    ids.add(str(aid))
    result = []
    for (date, publisher), ids in sorted(expected.items()):
        seen = observed.get((date, publisher), set())
        missing = ids - seen
        complete = bool(ids) and not missing
        result.append(dict(date_jst=date, publisher=publisher, expected_games=len(ids),
                           observed_games=len(seen), missing_games=len(missing), is_complete=int(complete),
                           missing_appids=";".join(sorted(missing, key=int)),
                           status="complete" if complete else "incomplete" if ids else "no_active_games"))
    return result
