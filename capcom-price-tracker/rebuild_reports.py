"""Recover immutable samples not yet consolidated and rebuild daily reports offline."""

from pathlib import Path

import collector as c


def rebuild(output: Path = c.DATA) -> dict:
    rows = c.read_csv(output / "powerbi" / "snapshots.csv")
    key = lambda r: (r["run_id"], r["publisher"], str(r["appid"]))
    samples = {key(row): row for row in rows}
    if len(samples) != len(rows):
        raise c.CollectionError("Duplicate consolidated snapshot keys")
    recovered = 0
    for path in sorted((output / "snapshots").glob("*.csv")):
        for row in c.read_csv(path):
            k = key(row)
            if k in samples:
                if samples[k] != row:
                    raise c.CollectionError("Conflicting immutable and consolidated snapshots")
            else:
                samples[k] = row
                recovered += 1
    combined = sorted(samples.values(), key=lambda x: (x["run_id"], int(x["appid"])))
    games, pubs = c.build_reports(combined, output)
    c.atomic_csv(output / "powerbi" / "snapshots.csv", c.SNAPSHOT_FIELDS, combined)
    c.atomic_csv(output / "powerbi" / "daily_games.csv", c.GAME_FIELDS, games)
    c.atomic_csv(output / "powerbi" / "daily_publishers.csv", c.PUBLISHER_FIELDS, pubs)
    return dict(recovered_samples=recovered, game_days=len(games), publisher_days=len(pubs))


if __name__ == "__main__":
    print(rebuild())
