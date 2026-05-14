# tests/test_formulation_snippets.py
"""
Regression tests for Task A — formulation snippet extraction.

These tests cover the pure extraction logic. They do NOT test the EPO API
path or the DB layer (those depend on credentials / live state).

Run with: python -m pytest tests/test_formulation_snippets.py
Or just: python tests/test_formulation_snippets.py
"""
import json
from modules.patent_fetcher import _extract_formulation_snippets


ALIASES = ["acetaminophen", "paracetamol", "Tylenol", "APAP"]


def test_extracts_sentence_with_drug_and_keyword():
    text = "The composition comprises acetaminophen as the active ingredient."
    out = _extract_formulation_snippets(text, ALIASES)
    assert len(out) == 1
    assert "acetaminophen" in out[0].lower()


def test_drops_sentence_with_drug_but_no_keyword():
    text = "Acetaminophen is an analgesic. The composition comprises sugar."
    out = _extract_formulation_snippets(text, ALIASES)
    # first sentence: drug but no keyword → drop
    # second sentence: keyword but no drug → drop
    assert out == []


def test_drops_sentence_with_keyword_but_no_drug():
    text = "The tablet is round. Excipients include lactose."
    assert _extract_formulation_snippets(text, ALIASES) == []


def test_case_insensitive_matching():
    text = "The TABLET contains PARACETAMOL and lactose."
    out = _extract_formulation_snippets(text, ALIASES)
    assert len(out) == 1


def test_alias_matching():
    text = "Tylenol formulation includes a binder. APAP tablets are common."
    out = _extract_formulation_snippets(text, ALIASES)
    assert len(out) == 2


def test_empty_input():
    assert _extract_formulation_snippets("", ALIASES) == []


def test_empty_aliases():
    text = "The tablet contains acetaminophen."
    assert _extract_formulation_snippets(text, []) == []


def test_hard_cap_of_20():
    # build 25 valid sentences
    sentence = "The composition comprises acetaminophen and excipients."
    text = " ".join([sentence] * 25)
    out = _extract_formulation_snippets(text, ALIASES)
    assert len(out) == 20  # cap from the function


def test_real_world_abstract():
    # Smoke test on the abstract from US6126967A that we verified manually.
    abstract = (
        "An extended release acetaminophen composition comprises a plurality "
        "of discrete particles containing acetaminophen which, when contained "
        "within a gelatin capsule, exhibits about 40 percent dissolution. "
        "After six hours, the contemplated extended release acetaminophen "
        "composition exhibits substantially complete dissolution. "
        "A process for treating a human patient with the extended release "
        "acetaminophen composition is also disclosed."
    )
    out = _extract_formulation_snippets(abstract, ALIASES)
    assert len(out) >= 2
    assert all("acetaminophen" in s.lower() for s in out)


def test_output_is_json_serializable():
    # The fetcher stores results as json.dumps(snippets); make sure that works.
    text = "The composition comprises acetaminophen and excipients."
    out = _extract_formulation_snippets(text, ALIASES)
    assert json.loads(json.dumps(out)) == out


if __name__ == "__main__":
    # Run without pytest
    import sys
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except AssertionError as e:
                print(f"FAIL {name}: {e}")
                sys.exit(1)
    print("All tests passed.")