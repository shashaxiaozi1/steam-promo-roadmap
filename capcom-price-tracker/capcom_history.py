"""Load approved Capcom histories; keep incomplete price/edition evidence in review."""

import csv
import hashlib
from pathlib import Path

from price_history import reconstruct


def load_history(folder: Path) -> list[dict]:
    manifest = folder / "review.csv"
    if not manifest.exists():
        return []
    with manifest.open(encoding="utf-8-sig", newline="") as f:
        entries = list(csv.DictReader(f))
    output, seen = [], set()
    for entry in entries:
        if entry["status"] != "approved":
            continue
        aid = int(entry["appid"])
        if aid in seen or entry["publisher"] != "CAPCOM":
            raise ValueError("Duplicate or non-Capcom entry in historical approval manifest")
        seen.add(aid)
        source = folder / "sources" / f"{aid}.csv"
        if hashlib.sha256(source.read_bytes()).hexdigest() != entry["source_sha256"]:
            raise ValueError(f"History source changed after review: {aid}")
        days, _, meta = reconstruct(source, entry, entry["list_price_usd"],
                                    coverage_start_utc=entry["coverage_start_utc"] or None,
                                    zero_price_is_unknown=True)
        if len(days) != int(entry["imported_days"]) or meta["sale_days"] != int(entry["sale_days"]):
            raise ValueError(f"History coverage differs from approval manifest: {aid}")
        output.extend(days)
    return output
