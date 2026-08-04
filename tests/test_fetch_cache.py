"""
test_fetch_cache.py — Task O Phase 1 regression tests.

Verifies that modules/patent_fetcher.py distinguishes permanent from
transient fetch failures, and never caches the transient ones.

FULLY OFFLINE. No EPO calls, no DB access. The production `client` and
`cache` module globals are swapped for a stub client and a throwaway
diskcache in a temp dir, then restored.

Deliberately does NOT use WO2020049327A1 (the patent whose ReadTimeout
motivated Task O) as a fixture. That timeout was transient — it will
likely succeed on retry, so a test depending on it would be
non-deterministic. It is the task's provenance, not its fixture.

Usage:
    python -m tests.test_fetch_cache

Refs: docs/spec/task_O.md §Phase 1
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

import diskcache

try:
    from modules import patent_fetcher as pf
except Exception as e:                                    # pragma: no cover
    print(f"[ERROR] cannot import modules.patent_fetcher: "
          f"{type(e).__name__}: {e}")
    print("        (module-level EPO client init needs .env credentials)")
    sys.exit(1)


PASS = 0
FAIL = 0


def section(name: str) -> None:
    print(f"\n── {name} " + "─" * max(0, 62 - len(name)))


def check(cond: bool, label: str) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {label}")
    else:
        FAIL += 1
        print(f"  ✗ {label}")


# ═══════════════════════════════════════════════════════════════════════════
# Stubs
# ═══════════════════════════════════════════════════════════════════════════

class FakeResponse:
    """Minimal stand-in for a requests.Response."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        self.text = ""


class HTTPErrorLike(Exception):
    """Exception carrying a .response, as requests.HTTPError does."""

    def __init__(self, status_code: int):
        super().__init__(f"{status_code} Server Error for url: https://ops.epo.org/...")
        self.response = FakeResponse(status_code)


class ReadTimeout(Exception):
    """Name-matched stand-in for requests.exceptions.ReadTimeout."""


class WeirdError(Exception):
    """An exception the classifier has never seen."""


CLAIMS_OK = {
    "ops:world-patent-data": {
        "ftxt:fulltext-documents": {
            "ftxt:fulltext-document": {
                "claims": [{
                    "@lang": "EN",
                    "claim": {"claim-text": [{"$": "A composition comprising X."}]},
                }]
            }
        }
    }
}


class StubClient:
    """
    Records call count; either raises a preset exception or returns a
    preset payload.
    """

    def __init__(self, raises: Exception | None = None, payload: dict | None = None):
        self.raises = raises
        self.payload = payload
        self.calls = 0

    def published_data(self, **kwargs):
        self.calls += 1
        if self.raises is not None:
            raise self.raises

        class R:
            text = ""

            def json(_self):
                return self.payload or {}

        return R()


class Swap:
    """Context manager: swap patent_fetcher's client / cache / sleep."""

    def __init__(self, client):
        self.client = client
        self.tmp = None
        self.saved = {}

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="taskO_test_cache_")
        self.saved = {
            "client": pf.client,
            "cache": pf.cache,
            "sleep": pf.time.sleep,
        }
        pf.client = self.client
        pf.cache = diskcache.Cache(self.tmp)
        pf.time.sleep = lambda *_a, **_k: None      # no real delays in tests
        return pf.cache

    def __exit__(self, *exc):
        try:
            pf.cache.close()
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)
            pf.client = self.saved["client"]
            pf.cache = self.saved["cache"]
            pf.time.sleep = self.saved["sleep"]
        return False


# ═══════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════

def test_classifier():
    section("classifier")
    c = pf._classify_fetch_failure

    check(c(HTTPErrorLike(404)) == "permanent", "404 → permanent")
    check(c(Exception("404 Client Error")) == "permanent",
          "404 in message (no .response) → permanent")
    check(c(ReadTimeout("read timed out")) == "transient", "ReadTimeout → transient")
    check(c(HTTPErrorLike(503)) == "transient", "503 → transient")
    check(c(HTTPErrorLike(429)) == "transient", "429 rate limit → transient")
    check(c(WeirdError("???")) == "transient",
          "unknown exception → transient (fail toward retry)")


FETCHERS = [
    ("claims",   "_fetch_claims",   "claims::"),
    ("abstract", "_fetch_abstract", "abstract::"),
    ("title",    "_fetch_title",    "title::"),
]


def test_transient_not_cached():
    section("transient failure is not cached")
    for label, fname, prefix in FETCHERS:
        fn = getattr(pf, fname)
        stub = StubClient(raises=ReadTimeout("read timed out"))
        with Swap(stub) as cache:
            result = fn("EP9999999A1")
            key = f"{prefix}EP9999999A1"
            check(result == "", f"{label}: returns empty on transient failure")
            check(key not in cache, f"{label}: cache key absent after transient failure")


def test_transient_retried():
    section("transient failure is retried on next call")
    for label, fname, _prefix in FETCHERS:
        fn = getattr(pf, fname)
        stub = StubClient(raises=ReadTimeout("read timed out"))
        with Swap(stub):
            fn("EP9999999A1")
            fn("EP9999999A1")
            check(stub.calls == 2,
                  f"{label}: client called twice (not short-circuited by cache), "
                  f"got {stub.calls}")


def test_permanent_cached():
    section("404 is cached (quota protection preserved)")
    for label, fname, prefix in FETCHERS:
        fn = getattr(pf, fname)
        stub = StubClient(raises=HTTPErrorLike(404))
        with Swap(stub) as cache:
            fn("US9999999B2")
            key = f"{prefix}US9999999B2"
            check(key in cache, f"{label}: cache key present after 404")
            check(cache.get(key) == "", f"{label}: cached value is empty string")
            fn("US9999999B2")
            check(stub.calls == 1,
                  f"{label}: second call served from cache, got {stub.calls} calls")


def test_success_unchanged():
    section("success path unchanged")
    stub = StubClient(payload=CLAIMS_OK)
    with Swap(stub) as cache:
        result = pf._fetch_claims("EP1234567B1")
        check("composition comprising" in result,
              "claims: parsed text returned")
        check(cache.get("claims::EP1234567B1") == result,
              "claims: success value cached")
        pf._fetch_claims("EP1234567B1")
        check(stub.calls == 1, f"claims: cache hit on repeat, got {stub.calls} calls")


def main() -> int:
    print("=" * 66)
    print("  test_fetch_cache — Task O Phase 1 (offline)")
    print("=" * 66)

    test_classifier()
    test_transient_not_cached()
    test_transient_retried()
    test_permanent_cached()
    test_success_unchanged()

    print()
    print("=" * 66)
    total = PASS + FAIL
    print(f"  Results: {PASS}/{total} passed, {FAIL} failed")
    print("  " + ("✓  ALL TESTS PASSED" if FAIL == 0 else "✗  FAILURES PRESENT"))
    print("=" * 66)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
