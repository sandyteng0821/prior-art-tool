"""
tests/test_api_smoke.py — HTTP smoke test for the API layer.

Zero-dependency (just urllib), runs against a live server.
Designed to grow incrementally as J-0..J-5 ship.

Usage:
    python tests/test_api_smoke.py [--base-url URL]

Default base URL: $API_BASE_URL or http://localhost:8007
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

# ── Test fixtures ────────────────────────────────────────────────────────────
# Change these if your DB content changes.
# KNOWN_PATENT must exist in the DB with non-empty claims and examples.
# KNOWN_ALIASES must produce at least one alias_count hit on KNOWN_PATENT.
# MISSING_PATENT must NOT exist in the DB.
KNOWN_PATENT = "US9415051B1"
KNOWN_ALIASES = ["Pemirolast", "BMY-26517"]
KNOWN_KEYWORDS = ["compris", "formulation", "excipient"]
MISSING_PATENT = "AU2020203515A1"
KNOWN_CONFIG = "pemirolast_ipf_v3"

def _url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}{path}"


def _get(url: str) -> tuple[int, dict]:
    try:
        resp = urllib.request.urlopen(url)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else "{}"
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, {"_raw": body}


def _post(url: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req)
        return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else "{}"
        try:
            return e.code, json.loads(body_text)
        except json.JSONDecodeError:
            return e.code, {"_raw": body_text}


class SmokeTest:
    def __init__(self, base_url: str):
        self.base = base_url
        self.passed = 0
        self.failed = 0

    def check(self, name: str, condition: bool, detail: str = ""):
        if condition:
            print(f"  ✓ {name}")
            self.passed += 1
        else:
            msg = f"  ✗ {name}"
            if detail:
                msg += f" ({detail})"
            print(msg)
            self.failed += 1

    def run(self):
        # ── J-0: Health check ────────────────────────────────────────
        print("[J-0] Health check")
        status, data = _get(_url(self.base, "/"))
        self.check("GET / → 200", status == 200, f"got {status}")
        self.check("status = running", data.get("status") == "running")
        self.check(
            "patents_count is int",
            isinstance(data.get("patents_count"), int),
        )

        # ── J-1: Database endpoints ──────────────────────────────────
        print(f"\n[J-1] DB patent lookup")
        status, data = _get(
            _url(self.base, f"/api/v1/db/patents/{KNOWN_PATENT}")
        )
        self.check("known patent → 200", status == 200, f"got {status}")
        self.check("found = true", data.get("found") is True)
        self.check(
            "has claims_chars > 0",
            data.get("claims_chars", 0) > 0,
        )

        status, data = _get(
            _url(self.base, f"/api/v1/db/patents/{KNOWN_PATENT}?detail=true&family=true")
        )
        self.check("detail+family → 200", status == 200)
        self.check("detail present", data.get("detail") is not None)
        self.check(
            "family_members is list",
            isinstance(data.get("family_members"), (list, type(None))),
        )

        status, data = _get(
            _url(self.base, "/api/v1/db/patents/NONEXISTENT999")
        )
        self.check("unknown patent → 404", status == 404, f"got {status}")
        self.check("found = false", data.get("found") is False)

        print("\n[J-1] DB stats")
        status, data = _get(_url(self.base, "/api/v1/db/stats"))
        self.check("stats → 200", status == 200)
        self.check(
            "total_patents > 0",
            data.get("total_patents", 0) > 0,
        )
        self.check(
            "by_source is dict",
            isinstance(data.get("by_source"), dict),
        )

        # ── J-2a: Inspect — DB hit ──────────────────────────────────
        print("\n[J-2a] Inspect — DB hit")
        status, data = _post(
            _url(self.base, "/api/v1/patents/inspect"),
            {
                "patent_id": KNOWN_PATENT,
                "drug_aliases": KNOWN_ALIASES,
                "keywords": KNOWN_KEYWORDS,
            },
        )
        self.check("inspect DB hit → 200", status == 200, f"got {status}")
        self.check(
            "data_source = db",
            data.get("data_source") == "db",
            f"got {data.get('data_source')}",
        )
        self.check("title present", bool(data.get("title")))
        self.check(
            "total_snippet_count > 0",
            data.get("total_snippet_count", 0) > 0,
            f"got {data.get('total_snippet_count')}",
        )
        self.check(
            f"alias_counts has {KNOWN_ALIASES[0]}",
            KNOWN_ALIASES[0] in data.get("alias_counts", {}),
        )
        self.check(
            "snippets has claims key",
            "claims" in data.get("snippets", {}),
        )

        # ── J-2b: Inspect — DB miss → EPO sandbox fallback ──────────
        print("\n[J-2b] Inspect — DB miss (EPO sandbox fallback)")
        status, data = _post(
            _url(self.base, "/api/v1/patents/inspect"),
            {
                "patent_id": MISSING_PATENT,
                "drug_aliases": ["test"],
            },
        )
        self.check("inspect DB miss → 200", status == 200, f"got {status}")
        self.check(
            "data_source = epo_sandbox",
            data.get("data_source") == "epo_sandbox",
            f"got {data.get('data_source')}",
        )
        self.check(
            "fallback_urls present",
            data.get("fallback_urls") is not None,
        )
        self.check(
            "fallback_urls contains patent ID",
            MISSING_PATENT in str(data.get("fallback_urls", {})),
        )

        # ── J-2a: Inspect — custom keywords ─────────────────────────
        print("\n[J-2a] Inspect — custom keywords")
        status, data = _post(
            _url(self.base, "/api/v1/patents/inspect"),
            {
                "patent_id": KNOWN_PATENT,
                "drug_aliases": [KNOWN_ALIASES[0]],
                "keywords": ["tablet", "oral"],
            },
        )
        self.check("custom kw → 200", status == 200)
        self.check(
            "data_source = db",
            data.get("data_source") == "db",
        )

        # ── J-2a: Inspect — source_filter ────────────────────────────
        print("\n[J-2a] Inspect — source_filter")
        status, data = _post(
            _url(self.base, "/api/v1/patents/inspect"),
            {
                "patent_id": KNOWN_PATENT,
                "drug_aliases": [KNOWN_ALIASES[0]],
                "source_filter": "claims",
            },
        )
        self.check("source_filter=claims → 200", status == 200)
        snippet_keys = list(data.get("snippets", {}).keys())
        self.check(
            "only claims in snippets",
            snippet_keys == ["claims"],
            f"got {snippet_keys}",
        )
        alias_keys = list(
            data.get("alias_counts", {}).get(KNOWN_ALIASES[0], {}).keys()
        )
        self.check(
            "alias_counts still has all sources",
            set(alias_keys) == {"claims", "examples", "abstract"},
            f"got {alias_keys}",
        )

        # ── J-2a: Inspect — validation ───────────────────────────────
        print("\n[J-2a] Inspect — validation")
        status, _ = _post(
            _url(self.base, "/api/v1/patents/inspect"),
            {"patent_id": "X"},  # missing drug_aliases
        )
        self.check("missing field → 422", status == 422, f"got {status}")

        # ── J-3: Score — dry-run ─────────────────────────────────────
        print("\n[J-3] Score — dry-run")
        status, data = _post(
            _url(self.base, "/api/v1/analysis/score"),
            {
                "patent_id": KNOWN_PATENT,
                "config_name": KNOWN_CONFIG,
                "dry_run": True,
            },
        )
        self.check("score dry-run → 200", status == 200, f"got {status}")
        self.check("dry_run = true", data.get("dry_run") is True)
        self.check(
            "db_state.title present",
            bool(data.get("db_state", {}).get("title")),
        )
        self.check(
            "screening_input present",
            data.get("screening_input") is not None,
        )
        self.check(
            "analysis_input present",
            data.get("analysis_input") is not None,
        )
        self.check(
            "screening is null (dry-run)",
            data.get("screening") is None,
        )

        # ── J-3: Score — dry-run stage 1 only ────────────────────────
        print("\n[J-3] Score — dry-run stage 1 only")
        status, data = _post(
            _url(self.base, "/api/v1/analysis/score"),
            {
                "patent_id": KNOWN_PATENT,
                "config_name": KNOWN_CONFIG,
                "dry_run": True,
                "stage": "1",
            },
        )
        self.check("stage 1 dry-run → 200", status == 200, f"got {status}")
        self.check(
            "screening_input present",
            data.get("screening_input") is not None,
        )
        self.check(
            "analysis_input absent",
            data.get("analysis_input") is None,
        )

        # ── J-3: Score — bad config ──────────────────────────────────
        print("\n[J-3] Score — bad config")
        status, data = _post(
            _url(self.base, "/api/v1/analysis/score"),
            {
                "patent_id": KNOWN_PATENT,
                "config_name": "nonexistent_config_xyz",
                "dry_run": True,
            },
        )
        self.check("bad config → 400", status == 400, f"got {status}")
        self.check(
            "detail mentions 'not found'",
            "not found" in data.get("detail", "").lower(),
        )

        # ── J-3: Score — patent not in DB ────────────────────────────
        print("\n[J-3] Score — patent not in DB")
        status, data = _post(
            _url(self.base, "/api/v1/analysis/score"),
            {
                "patent_id": "XX000000",
                "config_name": KNOWN_CONFIG,
                "dry_run": True,
            },
        )
        self.check("patent miss → 404", status == 404, f"got {status}")
        self.check(
            "detail mentions 'not in DB'",
            "not in DB" in data.get("detail", ""),
        )

        # ── Summary ──────────────────────────────────────────────────
        total = self.passed + self.failed
        print(f"\n{'='*50}")
        print(f"  {self.passed}/{total} passed", end="")
        if self.failed:
            print(f"  ({self.failed} FAILED)")
        else:
            print("  — all green ✓")
        print(f"{'='*50}")


def main():
    ap = argparse.ArgumentParser(description="API smoke test (J-0 through J-3)")
    ap.add_argument(
        "--base-url",
        default=os.environ.get("API_BASE_URL", "http://localhost:8007"),
        help="API base URL (default: $API_BASE_URL or http://localhost:8007)",
    )
    args = ap.parse_args()

    t = SmokeTest(args.base_url)
    t.run()
    sys.exit(1 if t.failed else 0)


if __name__ == "__main__":
    main()
