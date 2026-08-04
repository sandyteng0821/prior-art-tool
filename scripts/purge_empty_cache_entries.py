"""
purge_empty_cache_entries.py — Task O Phase 1, one-off.

Deletes `claims:: / abstract:: / title::` entries in the EPO diskcache
whose value is the empty string.

WHY THIS IS NEEDED
------------------
The Phase 1 fix changes cache *write* behaviour only. The read path
short-circuits first:

    if cache_key in cache:
        return cache[cache_key]

So every failure already cached as "" stays there, and the new
classification logic never runs for those patents. Without this purge,
the fix is inert on the existing cache.

WHAT IT DELETES
---------------
Every empty-valued entry under those three prefixes — including
legitimate 404 caches, which cannot be distinguished from poisoned
timeouts (that indistinguishability is the whole bug). Legitimate 404s
will simply be re-fetched and re-cached correctly, at a bounded one-time
quota cost.

Does NOT touch:
  - `search::` keys (list-valued, different lifecycle)
  - non-empty entries of any kind
  - cache/patents.db — no DB access at all
  - cache/epo_probe — probe cache, separate dir

NO AUDIT-LOG ENTRY
------------------
`scripts/_backfill_common.start_run()` writes to the `backfill_log` table
in patents.db. This script does not touch the DB, so writing a DB audit
row for a cache-only operation would misrepresent what happened. The
printed report plus `--json` is the record. (Deliberate departure from
the Task D backfill convention; noted here so it doesn't read as an
oversight.)

Usage:
    python -m scripts.purge_empty_cache_entries                 # dry-run
    python -m scripts.purge_empty_cache_entries --apply
    python -m scripts.purge_empty_cache_entries --apply --json scratch/purge_20260803.json

Refs: docs/spec/task_O.md §Phase 1 "Purge existing poisoned cache entries"
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

import diskcache

CACHE_DIR = "cache/epo"
PREFIXES = ("claims", "abstract", "title")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Purge empty-valued EPO cache entries")
    ap.add_argument("--cache-dir", default=CACHE_DIR)
    ap.add_argument("--apply", action="store_true",
                    help="actually delete. Without this, dry-run.")
    ap.add_argument("--json", help="write the report here")
    args = ap.parse_args(argv)

    if not Path(args.cache_dir).exists():
        print(f"[ERROR] cache dir not found: {args.cache_dir}")
        return 1

    dry_run = not args.apply
    if dry_run:
        print("[purge] No --apply given; dry-run (nothing will be deleted).")

    cache = diskcache.Cache(args.cache_dir)

    scanned = Counter()
    empty = Counter()
    other_prefixes = Counter()
    victims: list[str] = []
    unreadable: list[str] = []

    for key in list(cache.iterkeys()):
        if not isinstance(key, str) or "::" not in key:
            other_prefixes["<non-string or unprefixed>"] += 1
            continue
        prefix = key.split("::", 1)[0]
        if prefix not in PREFIXES:
            other_prefixes[prefix] += 1
            continue

        scanned[prefix] += 1
        try:
            value = cache.get(key)
        except Exception as e:
            # Never swallow — PROJECT_SKILL §3.2
            unreadable.append(key)
            print(f"[purge] could not read {key}: {type(e).__name__}: {e}")
            continue

        if value == "":
            empty[prefix] += 1
            victims.append(key)

    print()
    print("=" * 66)
    print(f"  purge_empty_cache_entries · {args.cache_dir}")
    print("=" * 66)
    print(f"{'prefix':<12}{'scanned':>10}{'empty':>10}{'':>4}")
    print("-" * 66)
    for prefix in PREFIXES:
        n = scanned[prefix]
        e = empty[prefix]
        pct = f"({e / n:.0%})" if n else ""
        print(f"{prefix:<12}{n:>10}{e:>10}   {pct}")
    print("-" * 66)
    print(f"{'TOTAL':<12}{sum(scanned.values()):>10}{sum(empty.values()):>10}")

    if other_prefixes:
        print(f"\n  untouched keys by prefix: {dict(other_prefixes)}")
    if unreadable:
        print(f"  unreadable keys: {len(unreadable)} (left in place)")

    deleted = 0
    if victims and args.apply:
        print(f"\n[purge] deleting {len(victims)} entries...")
        for key in victims:
            try:
                if cache.delete(key):
                    deleted += 1
            except Exception as e:
                print(f"[purge] delete failed for {key}: {type(e).__name__}: {e}")
        print(f"[purge] deleted {deleted}/{len(victims)}")
    elif victims:
        print(f"\n[purge] would delete {len(victims)} entries. Re-run with --apply.")
        for key in victims[:10]:
            print(f"    {key}")
        if len(victims) > 10:
            print(f"    ... and {len(victims) - 10} more")
    else:
        print("\n[purge] nothing to delete.")

    report = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "cache_dir": args.cache_dir,
        "dry_run": dry_run,
        "scanned": dict(scanned),
        "empty": dict(empty),
        "deleted": deleted,
        "unreadable": unreadable,
        "victims": victims if dry_run else [],
    }

    cache.close()

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[purge] report written to {p}")

    print("\n  Re-run after --apply: empty counts should all be 0.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
