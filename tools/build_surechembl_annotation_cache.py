#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build/query a patent-centric SureChEMBL annotation cache.

Purpose
-------
Given patent publication IDs, collect SureChEMBL annotations by document field:
  - chemical structures (all compounds)
  - known drugs (chemical structures matched to a local drug dictionary)
  - diseases (SureChEMBL biomedical entities with type_name = 'Disease')

This is a screening/index layer, NOT semantic prior-art interpretation. SureChEMBL
bulk data tells us that an entity was annotated in a field; it does not provide the
field's original full text or prove a drug-disease treatment relationship.

Field IDs (verified in the existing probe against the 2026-09-01 release):
  1 Description, 2 Claims, 3 Abstract, 4 Title

Examples
--------
Single patent (build in memory, print result):
  python3 build_surechembl_annotation_cache.py \
      --patent US9415051B1 \
      --data-dir ./sc_bulk \
      --drug-dictionary drug_inchikey.tsv

Batch cache:
  python3 build_surechembl_annotation_cache.py \
      --patents patent_ids.txt \
      --data-dir ./sc_bulk \
      --drug-dictionary drug_inchikey.tsv \
      --cache surechembl_annotations.duckdb \
      --tmp-dir ./duckdb_tmp --mem-limit 9GB

Cache-only lookup:
  python3 build_surechembl_annotation_cache.py \
      --lookup US9415051B1 \
      --cache surechembl_annotations.duckdb

Drug dictionary formats
-----------------------
A) Directly accepts resolve_inchikey.py output (no header required):
     pemirolast<TAB>AAAAAAAAAAAAAA[,BBBBBBBBBBBBBB]
   Keys may be 14-char skeletons or 27-char full InChIKeys.

B) Headered TSV:
     drug_name<TAB>inchikey<TAB>inchikey_skeleton<TAB>source

Dependencies: duckdb, pyarrow (pyarrow is required by the SureChEMBL parquet setup).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable


FIELD_NAMES = {1: "Description", 2: "Claims", 3: "Abstract", 4: "Title"}
FIELD_ORDER = ("Title", "Abstract", "Claims", "Description")
SCHEMA_VERSION = "0.1"

# Current validated boundary from the supplied SureChEMBL tool guide. Do not treat
# this as timeless: every new bulk release should revalidate it. CLI override exists.
DEFAULT_BIOMEDICAL_THROUGH = "2025-12-31"


# -----------------------------------------------------------------------------
# Logging / helpers
# -----------------------------------------------------------------------------

def log(msg: str = "") -> None:
    print(msg, flush=True)


def section(title: str) -> None:
    log()
    log(f"-- {title} " + "-" * max(1, 66 - len(title)))


def sql_quote(s: str | Path) -> str:
    return str(s).replace("'", "''")


def field_case(alias: str = "field_id") -> str:
    return (
        f"CASE {alias} "
        "WHEN 1 THEN 'Description' "
        "WHEN 2 THEN 'Claims' "
        "WHEN 3 THEN 'Abstract' "
        "WHEN 4 THEN 'Title' END"
    )


def timed_exec(con, label: str, sql: str, params=None):
    t0 = time.time()
    log(f"  ... {label}")
    con.execute(sql, params or [])
    log(f"  OK  {label}  ({time.time() - t0:.1f}s)")


# -----------------------------------------------------------------------------
# Patent ID normalization (kept aligned with probe_surechembl_bulk.py)
# -----------------------------------------------------------------------------

_PN_RE = re.compile(r"^([A-Z]{2})[-\s]?(\d+)[-\s]?([A-Z]\d?)?$", re.I)
_STRIP_LEADING_ZERO_CTRY = {"US"}


def canon_pn(raw: str) -> str:
    """Normalize patent number to the existing project's DB comparison form."""
    if not raw:
        return ""
    s = str(raw).strip().upper().replace(" ", "")
    m = _PN_RE.match(s)
    if not m:
        return s.replace("-", "")
    ctry, num, kind = m.group(1), m.group(2), (m.group(3) or "")
    if ctry in _STRIP_LEADING_ZERO_CTRY:
        num = num.lstrip("0") or "0"
    return f"{ctry}{num}{kind}"


@dataclass(frozen=True)
class PatentRequest:
    input_patent_number: str
    canonical_patent_number: str
    has_kind: bool
    valid_input: bool


def parse_patent_request(raw: str) -> PatentRequest:
    raw = str(raw or "").strip()
    m = _PN_RE.match(raw.upper().replace(" ", ""))
    return PatentRequest(
        input_patent_number=raw,
        canonical_patent_number=canon_pn(raw),
        has_kind=bool(m and m.group(3)),
        # Publication-level cache keys must include a kind code (A1/B1/B2...).
        # Without it, one numeric document number can resolve to multiple publications
        # and silently merge their annotations.
        valid_input=bool(m and m.group(3)),
    )


def load_patent_requests(single: str | None, path: Path | None) -> list[PatentRequest]:
    raw: list[str] = []
    if single:
        raw.append(single)
    if path:
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            # Allow a plain txt list or a first-column TSV/CSV export.
            first = re.split(r"[\t,]", line, maxsplit=1)[0].strip()
            if first:
                raw.append(first)

    # Dedupe by canonical ID, preserving the first original spelling.
    out: list[PatentRequest] = []
    seen: set[str] = set()
    for item in raw:
        r = parse_patent_request(item)
        key = r.canonical_patent_number or r.input_patent_number
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


# -----------------------------------------------------------------------------
# SureChEMBL parquet discovery
# -----------------------------------------------------------------------------

def find_table(data_dir: Path, table: str) -> list[Path]:
    hits = sorted(data_dir.glob(f"**/{table}*.parquet"))
    d = data_dir / table
    if d.is_dir():
        hits += sorted(d.glob("**/*.parquet"))
    return sorted(set(hits))


def parquet_src(files: list[Path]) -> str:
    if not files:
        raise ValueError("empty parquet source")
    quoted = [f"'{sql_quote(f)}'" for f in files]
    return quoted[0] if len(quoted) == 1 else "[" + ",".join(quoted) + "]"


def require_sources(data_dir: Path) -> dict[str, str]:
    needed = [
        "patents",
        "patent_compound_map",
        "compounds",
        "biomedical_entities",
        "biomedical_locations",
        "biomedical_types",
    ]
    out = {}
    for table in needed:
        files = find_table(data_dir, table)
        if not files:
            raise SystemExit(f"Missing SureChEMBL parquet table: {table} under {data_dir}")
        out[table] = parquet_src(files)
    return out


def infer_release_label(data_dir: Path) -> str:
    p = data_dir / "_release.txt"
    if p.exists():
        try:
            first = p.read_text(encoding="utf-8").splitlines()[0].strip()
            if first:
                return first
        except Exception:
            pass
    return "unknown"


# -----------------------------------------------------------------------------
# Drug dictionary
# -----------------------------------------------------------------------------

_IK27 = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")
_IK14 = re.compile(r"^[A-Z]{14}$")


@dataclass(frozen=True)
class DrugKey:
    drug_name: str
    exact_key: str | None
    skeleton: str
    source: str


def normalize_key(raw: str) -> tuple[str | None, str | None]:
    k = str(raw or "").strip().upper()
    if _IK27.match(k):
        return k, k[:14]
    if _IK14.match(k):
        return None, k
    return None, None


def _split_keys(cell: str) -> list[str]:
    return [x.strip() for x in re.split(r"[,;]", str(cell or "")) if x.strip()]


def load_drug_dictionary(path: Path) -> list[DrugKey]:
    """
    Supports resolver output and a richer headered TSV.

    Resolver output has comments and rows like:
      drug_name<TAB>key[,key...]
    """
    lines = path.read_text(encoding="utf-8-sig").splitlines()
    content = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not content:
        raise SystemExit(f"Drug dictionary is empty: {path}")

    first_cols = [x.strip().lower() for x in content[0].split("\t")]
    headered = "drug_name" in first_cols or "drug" in first_cols
    rows: list[DrugKey] = []

    if headered:
        reader = csv.DictReader(content, delimiter="\t")
        for rec in reader:
            # Case-insensitive header access.
            low = {str(k).strip().lower(): (v or "") for k, v in rec.items() if k is not None}
            drug = (low.get("drug_name") or low.get("drug") or low.get("name") or "").strip()
            source = (low.get("source") or "drug_dictionary").strip() or "drug_dictionary"
            if not drug:
                continue

            keys: list[str] = []
            keys.extend(_split_keys(low.get("inchikey", "")))
            keys.extend(_split_keys(low.get("inchi_key", "")))
            keys.extend(_split_keys(low.get("inchikey_skeleton", "")))
            keys.extend(_split_keys(low.get("skeleton", "")))
            for key in keys:
                exact, skel = normalize_key(key)
                if skel:
                    rows.append(DrugKey(drug, exact, skel, source))
    else:
        for ln in content:
            parts = ln.split("\t")
            drug = parts[0].strip()
            key_cell = parts[1] if len(parts) > 1 else ""
            if not drug:
                continue
            for key in _split_keys(key_cell):
                exact, skel = normalize_key(key)
                if skel:
                    rows.append(DrugKey(drug, exact, skel, "resolver_tsv"))

    # Dedupe exact duplicate dictionary entries.
    uniq: dict[tuple[str, str | None, str, str], DrugKey] = {}
    for r in rows:
        uniq[(r.drug_name.lower(), r.exact_key, r.skeleton, r.source)] = r
    out = list(uniq.values())
    if not out:
        raise SystemExit(
            f"No valid 14-char skeleton or 27-char InChIKey found in drug dictionary: {path}"
        )
    return out


# -----------------------------------------------------------------------------
# DuckDB/cache schema
# -----------------------------------------------------------------------------

def connect_duckdb(db_path: str, tmp_dir: Path, mem_limit: str | None, threads: int | None):
    try:
        import duckdb  # type: ignore
    except ImportError as e:
        raise SystemExit("duckdb is required. Install with: pip install duckdb pyarrow") from e

    tmp_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(db_path)
    con.execute(f"SET temp_directory='{sql_quote(tmp_dir)}'")
    con.execute("SET preserve_insertion_order=false")
    if mem_limit:
        con.execute(f"SET memory_limit='{sql_quote(mem_limit)}'")
    if threads:
        con.execute(f"SET threads={int(threads)}")
    return con


def init_cache_schema(con) -> None:
    con.execute("""
        CREATE TABLE IF NOT EXISTS cache_info (
            key VARCHAR PRIMARY KEY,
            value VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS patent_meta (
            input_patent_number VARCHAR,
            canonical_patent_number VARCHAR,
            patent_id BIGINT,
            patent_number VARCHAR,
            bulk_canonical_patent_number VARCHAR,
            country VARCHAR,
            publication_date_raw VARCHAR,
            publication_date DATE,
            family_id BIGINT,
            title VARCHAR,
            surechembl_release VARCHAR,
            status VARCHAR,
            biomedical_annotation_status VARCHAR,
            cached_at TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS patent_compound_annotation (
            canonical_patent_number VARCHAR,
            patent_id BIGINT,
            patent_number VARCHAR,
            field_id INTEGER,
            field_name VARCHAR,
            compound_id BIGINT,
            inchi_key VARCHAR,
            smiles VARCHAR,
            mol_weight DOUBLE,
            drug_name VARCHAR,
            is_known_drug BOOLEAN,
            drug_match_type VARCHAR,
            drug_source VARCHAR
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS patent_disease_annotation (
            canonical_patent_number VARCHAR,
            patent_id BIGINT,
            patent_number VARCHAR,
            field_id INTEGER,
            field_name VARCHAR,
            entity_id BIGINT,
            original_text VARCHAR,
            corrected_text VARCHAR,
            resolved_form VARCHAR,
            count BIGINT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS patent_annotation_summary (
            canonical_patent_number VARCHAR,
            status VARCHAR,
            biomedical_annotation_status VARCHAR,

            title_compound_count BIGINT,
            abstract_compound_count BIGINT,
            claims_compound_count BIGINT,
            description_compound_count BIGINT,

            title_known_drug_count BIGINT,
            abstract_known_drug_count BIGINT,
            claims_known_drug_count BIGINT,
            description_known_drug_count BIGINT,

            title_disease_count BIGINT,
            abstract_disease_count BIGINT,
            claims_disease_count BIGINT,
            description_disease_count BIGINT,

            has_known_drug BOOLEAN,
            has_disease BOOLEAN,
            has_drug_and_disease BOOLEAN,
            drug_disease_same_field BOOLEAN,
            drug_in_claims BOOLEAN,
            disease_in_claims BOOLEAN,
            drug_in_tiabcl BOOLEAN,
            disease_in_tiabcl BOOLEAN,
            drug_description_only BOOLEAN,
            disease_description_only BOOLEAN,
            large_compound_list BOOLEAN
        )
    """)


def upsert_cache_info(con, key: str, value: str) -> None:
    con.execute("DELETE FROM cache_info WHERE key = ?", [key])
    con.execute("INSERT INTO cache_info VALUES (?, ?)", [key, value])


def get_cache_info(con, key: str) -> str | None:
    row = con.execute("SELECT value FROM cache_info WHERE key = ?", [key]).fetchone()
    return str(row[0]) if row else None


def load_temp_requests(con, requests: list[PatentRequest]) -> None:
    con.execute("DROP TABLE IF EXISTS _requested_patents")
    con.execute("""
        CREATE TEMP TABLE _requested_patents (
            input_patent_number VARCHAR,
            canonical_patent_number VARCHAR,
            has_kind BOOLEAN,
            valid_input BOOLEAN
        )
    """)
    con.executemany(
        "INSERT INTO _requested_patents VALUES (?, ?, ?, ?)",
        [
            (
                r.input_patent_number,
                r.canonical_patent_number,
                r.has_kind,
                r.valid_input,
            )
            for r in requests
        ],
    )


def load_temp_drug_dictionary(con, rows: list[DrugKey]) -> None:
    con.execute("DROP TABLE IF EXISTS _drug_dictionary")
    con.execute("""
        CREATE TEMP TABLE _drug_dictionary (
            drug_name VARCHAR,
            exact_key VARCHAR,
            skeleton VARCHAR,
            source VARCHAR
        )
    """)
    con.executemany(
        "INSERT INTO _drug_dictionary VALUES (?, ?, ?, ?)",
        [(r.drug_name, r.exact_key, r.skeleton, r.source) for r in rows],
    )


def clear_requested_rows(con) -> None:
    for table in (
        "patent_annotation_summary",
        "patent_disease_annotation",
        "patent_compound_annotation",
        "patent_meta",
    ):
        con.execute(
            f"DELETE FROM {table} WHERE canonical_patent_number IN "
            "(SELECT canonical_patent_number FROM _requested_patents)"
        )


# -----------------------------------------------------------------------------
# Build cache
# -----------------------------------------------------------------------------

def resolve_target_patents(con, patents_src: str) -> None:
    con.execute("DROP TABLE IF EXISTS _target_patents")
    # Bulk patent_number is CC-PATNO-KK. Existing probe verified US bulk IDs are
    # not GPSS's zero-padded grant form, so Python-side canonical input can join
    # directly after removing hyphens/spaces from the bulk value.
    con.execute(f"""
        CREATE TEMP TABLE _target_patents AS
        WITH p AS (
            SELECT
                id AS patent_id,
                patent_number,
                upper(replace(replace(patent_number, '-', ''), ' ', '')) AS bulk_canon,
                country,
                CAST(publication_date AS VARCHAR) AS publication_date_raw,
                CASE
                    WHEN TRY_CAST(publication_date AS DATE) > DATE '1800-01-01'
                    THEN TRY_CAST(publication_date AS DATE)
                    ELSE NULL
                END AS publication_date,
                family_id,
                title
            FROM read_parquet({patents_src})
        )
        SELECT
            r.input_patent_number,
            r.canonical_patent_number,
            p.patent_id,
            p.patent_number,
            p.bulk_canon,
            p.country,
            p.publication_date_raw,
            p.publication_date,
            p.family_id,
            p.title
        FROM _requested_patents r
        JOIN p
          ON r.valid_input
         AND p.bulk_canon = r.canonical_patent_number
    """)


def insert_patent_meta(
    con,
    release: str,
    biomedical_through: str,
) -> None:
    boundary = date.fromisoformat(biomedical_through)
    cached_at = datetime.now(timezone.utc).replace(tzinfo=None)

    # Found rows.
    con.execute("""
        INSERT INTO patent_meta
        SELECT
            input_patent_number,
            canonical_patent_number,
            patent_id,
            patent_number,
            bulk_canon,
            country,
            publication_date_raw,
            publication_date,
            family_id,
            title,
            ?,
            'found',
            CASE
                WHEN publication_date IS NULL THEN 'unknown'
                WHEN publication_date <= ? THEN 'available'
                ELSE 'outside_known_coverage'
            END,
            ?
        FROM _target_patents
    """, [release, boundary, cached_at])

    # Invalid / not found rows. They are intentionally NOT represented as zero
    # annotation patents; status carries the difference.
    con.execute("""
        INSERT INTO patent_meta
        SELECT
            r.input_patent_number,
            r.canonical_patent_number,
            NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
            ?,
            CASE WHEN r.valid_input THEN 'not_in_bulk_patents'
                 ELSE 'input_not_resolved' END,
            'unknown',
            ?
        FROM _requested_patents r
        WHERE NOT EXISTS (
            SELECT 1 FROM _target_patents t
            WHERE t.canonical_patent_number = r.canonical_patent_number
        )
    """, [release, cached_at])


def insert_compound_annotations(con, pcm_src: str, compounds_src: str) -> None:
    con.execute("DROP TABLE IF EXISTS _compound_raw")
    timed_exec(con, "scan target chemical annotations", f"""
        CREATE TEMP TABLE _compound_raw AS
        SELECT DISTINCT
            t.canonical_patent_number,
            t.patent_id,
            t.patent_number,
            m.field_id,
            {field_case('m.field_id')} AS field_name,
            c.id AS compound_id,
            upper(c.inchi_key) AS inchi_key,
            c.smiles,
            TRY_CAST(c.mol_weight AS DOUBLE) AS mol_weight
        FROM read_parquet({pcm_src}) m
        JOIN _target_patents t ON t.patent_id = m.patent_id
        JOIN read_parquet({compounds_src}) c ON c.id = m.compound_id
        WHERE m.field_id IN (1,2,3,4)
    """)

    con.execute("DROP TABLE IF EXISTS _drug_matches")
    con.execute("""
        CREATE TEMP TABLE _drug_matches AS
        WITH candidates AS (
            SELECT
                r.canonical_patent_number,
                r.patent_id,
                r.field_id,
                r.compound_id,
                d.drug_name,
                d.source,
                CASE
                    WHEN d.exact_key IS NOT NULL AND r.inchi_key = d.exact_key
                    THEN 'exact'
                    ELSE 'skeleton'
                END AS match_type,
                CASE
                    WHEN d.exact_key IS NOT NULL AND r.inchi_key = d.exact_key
                    THEN 1 ELSE 2
                END AS priority
            FROM _compound_raw r
            JOIN _drug_dictionary d
              ON (d.exact_key IS NOT NULL AND r.inchi_key = d.exact_key)
              OR substr(r.inchi_key, 1, 14) = d.skeleton
            WHERE r.inchi_key IS NOT NULL
        ), ranked AS (
            SELECT *,
                   row_number() OVER (
                       PARTITION BY canonical_patent_number, patent_id, field_id,
                                    compound_id, lower(drug_name)
                       ORDER BY priority, source
                   ) AS rn
            FROM candidates
        )
        SELECT canonical_patent_number, patent_id, field_id, compound_id,
               drug_name, source, match_type
        FROM ranked WHERE rn = 1
    """)

    timed_exec(con, "write compound/known-drug cache", """
        INSERT INTO patent_compound_annotation
        SELECT
            r.canonical_patent_number,
            r.patent_id,
            r.patent_number,
            r.field_id,
            r.field_name,
            r.compound_id,
            r.inchi_key,
            r.smiles,
            r.mol_weight,
            d.drug_name,
            d.drug_name IS NOT NULL AS is_known_drug,
            d.match_type AS drug_match_type,
            d.source AS drug_source
        FROM _compound_raw r
        LEFT JOIN _drug_matches d
          ON d.canonical_patent_number = r.canonical_patent_number
         AND d.patent_id = r.patent_id
         AND d.field_id = r.field_id
         AND d.compound_id = r.compound_id
    """)


def insert_disease_annotations(
    con,
    locations_src: str,
    entities_src: str,
    types_src: str,
) -> None:
    timed_exec(con, "scan/write target Disease annotations", f"""
        INSERT INTO patent_disease_annotation
        SELECT
            t.canonical_patent_number,
            t.patent_id,
            t.patent_number,
            l.field_id,
            {field_case('l.field_id')} AS field_name,
            e.id AS entity_id,
            e.original_text,
            e.corrected_text,
            e.resolved_form,
            sum(COALESCE(l.count, 0))::BIGINT AS count
        FROM read_parquet({locations_src}) l
        JOIN _target_patents t ON t.patent_id = l.patent_id
        JOIN read_parquet({entities_src}) e ON e.id = l.entity_id
        JOIN read_parquet({types_src}) ty ON ty.id = e.type_id
        WHERE l.field_id IN (1,2,3,4)
          AND lower(ty.type_name) = 'disease'
        GROUP BY
            t.canonical_patent_number, t.patent_id, t.patent_number,
            l.field_id, e.id, e.original_text, e.corrected_text, e.resolved_form
    """)


def rebuild_requested_summary(con, large_compound_threshold: int | None) -> None:
    threshold_expr = (
        f"(COALESCE(c.description_compound_count, 0) >= {int(large_compound_threshold)})"
        if large_compound_threshold is not None
        else "CAST(NULL AS BOOLEAN)"
    )

    timed_exec(con, "build patent summary", f"""
        INSERT INTO patent_annotation_summary
        WITH requested_meta AS (
            SELECT * FROM patent_meta
            WHERE canonical_patent_number IN
                  (SELECT canonical_patent_number FROM _requested_patents)
        ),
        c AS (
            SELECT canonical_patent_number,
                count(DISTINCT compound_id) FILTER (WHERE field_id = 4) AS title_compound_count,
                count(DISTINCT compound_id) FILTER (WHERE field_id = 3) AS abstract_compound_count,
                count(DISTINCT compound_id) FILTER (WHERE field_id = 2) AS claims_compound_count,
                count(DISTINCT compound_id) FILTER (WHERE field_id = 1) AS description_compound_count,
                count(DISTINCT drug_name) FILTER (WHERE field_id = 4 AND is_known_drug) AS title_known_drug_count,
                count(DISTINCT drug_name) FILTER (WHERE field_id = 3 AND is_known_drug) AS abstract_known_drug_count,
                count(DISTINCT drug_name) FILTER (WHERE field_id = 2 AND is_known_drug) AS claims_known_drug_count,
                count(DISTINCT drug_name) FILTER (WHERE field_id = 1 AND is_known_drug) AS description_known_drug_count,
                count(DISTINCT drug_name) FILTER (WHERE is_known_drug) AS known_drug_count,
                max(CASE WHEN field_id = 2 AND is_known_drug THEN 1 ELSE 0 END) AS drug_claims,
                max(CASE WHEN field_id IN (2,3,4) AND is_known_drug THEN 1 ELSE 0 END) AS drug_tiabcl,
                max(CASE WHEN field_id = 1 AND is_known_drug THEN 1 ELSE 0 END) AS drug_desc
            FROM patent_compound_annotation
            WHERE canonical_patent_number IN
                  (SELECT canonical_patent_number FROM _requested_patents)
            GROUP BY canonical_patent_number
        ),
        d AS (
            SELECT canonical_patent_number,
                count(DISTINCT COALESCE(NULLIF(resolved_form,''), corrected_text)) FILTER (WHERE field_id = 4) AS title_disease_count,
                count(DISTINCT COALESCE(NULLIF(resolved_form,''), corrected_text)) FILTER (WHERE field_id = 3) AS abstract_disease_count,
                count(DISTINCT COALESCE(NULLIF(resolved_form,''), corrected_text)) FILTER (WHERE field_id = 2) AS claims_disease_count,
                count(DISTINCT COALESCE(NULLIF(resolved_form,''), corrected_text)) FILTER (WHERE field_id = 1) AS description_disease_count,
                count(DISTINCT COALESCE(NULLIF(resolved_form,''), corrected_text)) AS disease_count,
                max(CASE WHEN field_id = 2 THEN 1 ELSE 0 END) AS disease_claims,
                max(CASE WHEN field_id IN (2,3,4) THEN 1 ELSE 0 END) AS disease_tiabcl,
                max(CASE WHEN field_id = 1 THEN 1 ELSE 0 END) AS disease_desc
            FROM patent_disease_annotation
            WHERE canonical_patent_number IN
                  (SELECT canonical_patent_number FROM _requested_patents)
            GROUP BY canonical_patent_number
        ),
        samef AS (
            SELECT DISTINCT c.canonical_patent_number
            FROM patent_compound_annotation c
            JOIN patent_disease_annotation d
              ON d.canonical_patent_number = c.canonical_patent_number
             AND d.field_id = c.field_id
            WHERE c.is_known_drug
              AND c.canonical_patent_number IN
                  (SELECT canonical_patent_number FROM _requested_patents)
        )
        SELECT
            m.canonical_patent_number,
            m.status,
            m.biomedical_annotation_status,

            COALESCE(c.title_compound_count, 0),
            COALESCE(c.abstract_compound_count, 0),
            COALESCE(c.claims_compound_count, 0),
            COALESCE(c.description_compound_count, 0),

            COALESCE(c.title_known_drug_count, 0),
            COALESCE(c.abstract_known_drug_count, 0),
            COALESCE(c.claims_known_drug_count, 0),
            COALESCE(c.description_known_drug_count, 0),

            COALESCE(d.title_disease_count, 0),
            COALESCE(d.abstract_disease_count, 0),
            COALESCE(d.claims_disease_count, 0),
            COALESCE(d.description_disease_count, 0),

            COALESCE(c.known_drug_count, 0) > 0,
            CASE
                WHEN COALESCE(d.disease_count, 0) > 0 THEN TRUE
                WHEN m.biomedical_annotation_status = 'available' THEN FALSE
                ELSE NULL
            END,
            CASE
                WHEN COALESCE(c.known_drug_count, 0) = 0 THEN FALSE
                WHEN COALESCE(d.disease_count, 0) > 0 THEN TRUE
                WHEN m.biomedical_annotation_status = 'available' THEN FALSE
                ELSE NULL
            END,
            CASE
                WHEN samef.canonical_patent_number IS NOT NULL THEN TRUE
                WHEN COALESCE(c.known_drug_count, 0) = 0 THEN FALSE
                WHEN m.biomedical_annotation_status = 'available' THEN FALSE
                ELSE NULL
            END,
            COALESCE(c.drug_claims, 0) > 0,
            CASE
                WHEN COALESCE(d.disease_claims, 0) > 0 THEN TRUE
                WHEN m.biomedical_annotation_status = 'available' THEN FALSE
                ELSE NULL
            END,
            COALESCE(c.drug_tiabcl, 0) > 0,
            CASE
                WHEN COALESCE(d.disease_tiabcl, 0) > 0 THEN TRUE
                WHEN m.biomedical_annotation_status = 'available' THEN FALSE
                ELSE NULL
            END,
            COALESCE(c.drug_desc, 0) > 0 AND COALESCE(c.drug_tiabcl, 0) = 0,
            CASE
                WHEN COALESCE(d.disease_count, 0) > 0
                THEN COALESCE(d.disease_desc, 0) > 0 AND COALESCE(d.disease_tiabcl, 0) = 0
                WHEN m.biomedical_annotation_status = 'available' THEN FALSE
                ELSE NULL
            END,
            {threshold_expr}
        FROM requested_meta m
        LEFT JOIN c ON c.canonical_patent_number = m.canonical_patent_number
        LEFT JOIN d ON d.canonical_patent_number = m.canonical_patent_number
        LEFT JOIN samef ON samef.canonical_patent_number = m.canonical_patent_number
    """)


def build_cache(
    con,
    data_dir: Path,
    requests: list[PatentRequest],
    drug_rows: list[DrugKey],
    release: str,
    biomedical_through: str,
    large_compound_threshold: int | None,
) -> dict:
    sources = require_sources(data_dir)
    init_cache_schema(con)
    load_temp_requests(con, requests)
    load_temp_drug_dictionary(con, drug_rows)

    clear_requested_rows(con)

    section("Resolve target patents")
    t0 = time.time()
    log("  ... create target_patents")
    resolve_target_patents(con, sources["patents"])
    n_target = con.execute("SELECT count(*) FROM _target_patents").fetchone()[0]
    log(f"  OK  target rows: {n_target:,}  ({time.time() - t0:.1f}s)")

    insert_patent_meta(con, release, biomedical_through)

    section("Chemical annotations")
    insert_compound_annotations(con, sources["patent_compound_map"], sources["compounds"])

    section("Disease annotations")
    insert_disease_annotations(
        con,
        sources["biomedical_locations"],
        sources["biomedical_entities"],
        sources["biomedical_types"],
    )

    section("Summary")
    rebuild_requested_summary(con, large_compound_threshold)

    upsert_cache_info(con, "schema_version", SCHEMA_VERSION)
    upsert_cache_info(con, "surechembl_release", release)
    upsert_cache_info(con, "biomedical_annotation_through", biomedical_through)
    upsert_cache_info(con, "updated_at", datetime.now(timezone.utc).isoformat())

    stats = con.execute("""
        SELECT
            count(*) AS requested,
            sum(CASE WHEN status = 'found' THEN 1 ELSE 0 END) AS found,
            sum(CASE WHEN status = 'not_in_bulk_patents' THEN 1 ELSE 0 END) AS not_in_bulk,
            sum(CASE WHEN status = 'input_not_resolved' THEN 1 ELSE 0 END) AS invalid
        FROM patent_meta
        WHERE canonical_patent_number IN
              (SELECT canonical_patent_number FROM _requested_patents)
    """).fetchone()
    return {
        "requested": int(stats[0] or 0),
        "found": int(stats[1] or 0),
        "not_in_bulk_patents": int(stats[2] or 0),
        "input_not_resolved": int(stats[3] or 0),
        "release": release,
        "biomedical_annotation_through": biomedical_through,
    }


# -----------------------------------------------------------------------------
# Cache lookup / presentation
# -----------------------------------------------------------------------------

def _rows_as_dicts(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def lookup_payload(con, patent_number: str) -> dict | None:
    want = canon_pn(patent_number)
    meta_rows = _rows_as_dicts(con.execute("""
        SELECT * FROM patent_meta
        WHERE canonical_patent_number = ?
           OR bulk_canonical_patent_number = ?
        ORDER BY CASE WHEN canonical_patent_number = ? THEN 0 ELSE 1 END
    """, [want, want, want]))
    if not meta_rows:
        return None

    # If a no-kind query matched multiple publications, preserve all metadata but
    # annotations are grouped under the requested canonical key.
    canonical = meta_rows[0]["canonical_patent_number"]
    summary_rows = _rows_as_dicts(con.execute(
        "SELECT * FROM patent_annotation_summary WHERE canonical_patent_number = ?",
        [canonical],
    ))

    comp = _rows_as_dicts(con.execute("""
        SELECT field_id, field_name, compound_id, inchi_key, smiles, mol_weight,
               drug_name, is_known_drug, drug_match_type, drug_source
        FROM patent_compound_annotation
        WHERE canonical_patent_number = ?
        ORDER BY field_id DESC, is_known_drug DESC, lower(drug_name), compound_id
    """, [canonical]))
    disease = _rows_as_dicts(con.execute("""
        SELECT field_id, field_name, entity_id, original_text, corrected_text,
               resolved_form, count
        FROM patent_disease_annotation
        WHERE canonical_patent_number = ?
        ORDER BY field_id DESC, count DESC, lower(corrected_text)
    """, [canonical]))

    fields = {
        name: {
            "compound_count": 0,
            "known_drugs": [],
            "diseases": [],
        }
        for name in FIELD_ORDER
    }

    # Count raw unique compounds independently of dictionary expansion.
    compound_sets: dict[str, set[int]] = {name: set() for name in FIELD_ORDER}
    drug_buckets: dict[str, dict[str, dict]] = {name: {} for name in FIELD_ORDER}
    for r in comp:
        fname = r["field_name"]
        if fname not in fields:
            continue
        if r["compound_id"] is not None:
            compound_sets[fname].add(int(r["compound_id"]))
        if not r["is_known_drug"] or not r["drug_name"]:
            continue
        key = str(r["drug_name"]).lower()
        b = drug_buckets[fname].setdefault(key, {
            "name": r["drug_name"],
            "compound_ids": set(),
            "inchikeys": set(),
            "match_types": set(),
            "sources": set(),
        })
        if r["compound_id"] is not None:
            b["compound_ids"].add(int(r["compound_id"]))
        if r["inchi_key"]:
            b["inchikeys"].add(r["inchi_key"])
        if r["drug_match_type"]:
            b["match_types"].add(r["drug_match_type"])
        if r["drug_source"]:
            b["sources"].add(r["drug_source"])

    for fname in FIELD_ORDER:
        fields[fname]["compound_count"] = len(compound_sets[fname])
        for b in sorted(drug_buckets[fname].values(), key=lambda x: str(x["name"]).lower()):
            fields[fname]["known_drugs"].append({
                "name": b["name"],
                "compound_ids": sorted(b["compound_ids"]),
                "inchikeys": sorted(b["inchikeys"]),
                "match_types": sorted(b["match_types"]),
                "sources": sorted(b["sources"]),
            })

    # Collapse disease spelling variants by resolved_form when available; retain
    # names/entity IDs and summed occurrence count so information is not lost.
    disease_buckets: dict[str, dict[str, dict]] = {name: {} for name in FIELD_ORDER}
    for r in disease:
        fname = r["field_name"]
        if fname not in fields:
            continue
        identity = str(r["resolved_form"] or r["corrected_text"] or r["entity_id"])
        b = disease_buckets[fname].setdefault(identity, {
            "name": r["corrected_text"],
            "mesh_id": r["resolved_form"],
            "count": 0,
            "entity_ids": set(),
            "variants": set(),
        })
        b["count"] += int(r["count"] or 0)
        if r["entity_id"] is not None:
            b["entity_ids"].add(int(r["entity_id"]))
        if r["corrected_text"]:
            b["variants"].add(r["corrected_text"])

    for fname in FIELD_ORDER:
        vals = sorted(
            disease_buckets[fname].values(),
            key=lambda x: (-x["count"], str(x["name"] or "").lower()),
        )
        for b in vals:
            fields[fname]["diseases"].append({
                "name": b["name"],
                "mesh_id": b["mesh_id"],
                "count": b["count"],
                "entity_ids": sorted(b["entity_ids"]),
                "variants": sorted(b["variants"]),
            })

    return {
        "query": patent_number,
        "canonical_patent_number": canonical,
        "patents": meta_rows,
        "fields": fields,
        "summary": summary_rows[0] if summary_rows else None,
        "cache": {
            "schema_version": get_cache_info(con, "schema_version"),
            "surechembl_release": get_cache_info(con, "surechembl_release"),
            "biomedical_annotation_through": get_cache_info(con, "biomedical_annotation_through"),
        },
    }


def print_lookup(payload: dict) -> None:
    meta = payload["patents"][0]
    log()
    log(f"Patent: {payload['canonical_patent_number']}")
    log(f"Status: {meta.get('status')}")
    if meta.get("patent_number"):
        log(f"SureChEMBL: {meta.get('patent_number')}  |  release {meta.get('surechembl_release')}")
    if meta.get("title"):
        log(f"Title: {meta.get('title')}")
    log(f"Biomedical coverage: {meta.get('biomedical_annotation_status')}")

    if meta.get("status") != "found":
        if meta.get("status") == "not_in_bulk_patents":
            log("  Note: not in patents.parquet != patent does not exist; this bulk table is the chemical-extraction subset.")
        return

    bio_status = meta.get("biomedical_annotation_status")
    bio_negative_reliable = bio_status == "available"
    for fname in FIELD_ORDER:
        f = payload["fields"][fname]
        log()
        log(fname)
        log(f"  compounds: {f['compound_count']}")
        if f["known_drugs"]:
            log("  known drugs:")
            for d in f["known_drugs"]:
                mt = "/".join(d["match_types"]) or "?"
                log(f"    - {d['name']}  [{mt}; {len(d['compound_ids'])} compound(s)]")
        else:
            log("  known drugs: -")

        if f["diseases"]:
            log("  diseases:")
            for d in f["diseases"]:
                mesh = f" [{d['mesh_id']}]" if d["mesh_id"] else ""
                log(f"    - {d['name']}{mesh}  (count={d['count']})")
        elif not bio_negative_reliable:
            log("  diseases: [none annotated; absence is not reliable for this publication period]")
        else:
            log("  diseases: -")

    s = payload.get("summary") or {}
    log()
    log("Summary")
    log(f"  has known drug: {bool(s.get('has_known_drug'))}")
    hd = s.get("has_disease")
    log(f"  has disease: {'unknown' if hd is None else bool(hd)}")
    sf = s.get("drug_disease_same_field")
    log(f"  drug + disease same field: {'unknown' if sf is None else bool(sf)}")
    log(f"  drug in claims: {bool(s.get('drug_in_claims'))}")
    dc = s.get("disease_in_claims")
    log(f"  disease in claims: {'unknown' if dc is None else bool(dc)}")
    if s.get("large_compound_list") is not None:
        log(f"  large compound list: {bool(s.get('large_compound_list'))}")


def json_default(v):
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, set):
        return sorted(v)
    return str(v)


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description="Build/query a patent-centric SureChEMBL field annotation cache"
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--patent", metavar="PN", help="build annotations for one patent and print")
    mode.add_argument("--patents", type=Path, metavar="FILE", help="batch patent IDs (one per line or first TSV/CSV column)")
    mode.add_argument("--lookup", metavar="PN", help="lookup an already-built cache only")

    ap.add_argument("--data-dir", type=Path, default=Path("./sc_bulk"), help="SureChEMBL bulk parquet directory")
    ap.add_argument("--drug-dictionary", type=Path, help="resolver TSV or headered drug dictionary TSV")
    ap.add_argument("--cache", type=Path, help="DuckDB cache path; required for --patents")
    ap.add_argument("--replace-cache", action="store_true", help="delete existing cache before build")
    ap.add_argument("--release-label", default=None, help="override SureChEMBL release label; otherwise read data-dir/_release.txt")
    ap.add_argument(
        "--biomedical-through",
        default=DEFAULT_BIOMEDICAL_THROUGH,
        metavar="YYYY-MM-DD",
        help=(
            "last date with validated biomedical annotation coverage; default from current tool guide: "
            + DEFAULT_BIOMEDICAL_THROUGH
            + ". Revalidate/override for a new release"
        ),
    )
    ap.add_argument(
        "--large-compound-threshold",
        type=int,
        default=None,
        help="optional calibrated Description compound-count threshold; default leaves flag NULL",
    )
    ap.add_argument("--tmp-dir", type=Path, default=Path("./duckdb_tmp"), help="DuckDB spill directory")
    ap.add_argument("--mem-limit", default=None, help="DuckDB memory_limit, e.g. 9GB")
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--out", type=Path, help="write lookup/build result JSON")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    # Cache-only path must not depend on original parquet data or drug dictionary.
    if args.lookup:
        if not args.cache:
            raise SystemExit("--lookup requires --cache")
        if not args.cache.exists():
            raise SystemExit(f"Cache does not exist: {args.cache}")
        con = connect_duckdb(str(args.cache), args.tmp_dir, args.mem_limit, args.threads)
        init_cache_schema(con)
        payload = lookup_payload(con, args.lookup)
        if payload is None:
            log(f"Not cached: {canon_pn(args.lookup)}")
            return 2
        print_lookup(payload)
        if args.out:
            args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
            log(f"\nJSON -> {args.out}")
        return 0

    if args.patents and not args.cache:
        raise SystemExit("--patents batch mode requires --cache")
    if not args.drug_dictionary:
        raise SystemExit("build mode requires --drug-dictionary (all compounds cannot be assumed to be drugs)")
    if not args.data_dir.exists():
        raise SystemExit(f"--data-dir does not exist: {args.data_dir}")
    if not args.drug_dictionary.exists():
        raise SystemExit(f"--drug-dictionary does not exist: {args.drug_dictionary}")

    # Validate date early.
    try:
        date.fromisoformat(args.biomedical_through)
    except ValueError as e:
        raise SystemExit("--biomedical-through must be YYYY-MM-DD") from e

    requests = load_patent_requests(args.patent, args.patents)
    if not requests:
        raise SystemExit("No patent IDs loaded")
    drug_rows = load_drug_dictionary(args.drug_dictionary)
    drug_dictionary_sha256 = hashlib.sha256(args.drug_dictionary.read_bytes()).hexdigest()

    release = args.release_label or infer_release_label(args.data_dir)
    db_path = ":memory:"
    if args.cache:
        if args.replace_cache and args.cache.exists():
            args.cache.unlink()
        args.cache.parent.mkdir(parents=True, exist_ok=True)
        db_path = str(args.cache)

    section("Inputs")
    log(f"  patents: {len(requests):,}")
    log(f"  drug dictionary keys: {len(drug_rows):,}")
    log(f"  release: {release}")
    log(f"  biomedical annotation through: {args.biomedical_through}")
    if args.biomedical_through == DEFAULT_BIOMEDICAL_THROUGH:
        log("  WARNING: coverage boundary is the currently validated guide value; revalidate for a newer release")

    con = connect_duckdb(db_path, args.tmp_dir, args.mem_limit, args.threads)
    init_cache_schema(con)

    # One cache should represent one SureChEMBL release. Mixing releases makes
    # absence/coverage semantics hard to audit.
    old_release = get_cache_info(con, "surechembl_release")
    if old_release and old_release != "unknown" and release != "unknown" and old_release != release:
        raise SystemExit(
            f"Cache release mismatch: cache={old_release}, input={release}. "
            "Use a new cache or --replace-cache."
        )
    old_dict_hash = get_cache_info(con, "drug_dictionary_sha256")
    if old_dict_hash and old_dict_hash != drug_dictionary_sha256:
        raise SystemExit(
            "Drug dictionary differs from the one used to build this cache. "
            "Use a new cache or --replace-cache so known-drug annotations stay consistent."
        )

    stats = build_cache(
        con,
        args.data_dir,
        requests,
        drug_rows,
        release,
        args.biomedical_through,
        args.large_compound_threshold,
    )
    upsert_cache_info(con, "drug_dictionary_sha256", drug_dictionary_sha256)
    upsert_cache_info(con, "drug_dictionary_file", args.drug_dictionary.name)

    section("Done")
    log(json.dumps(stats, ensure_ascii=False, indent=2))
    if args.cache:
        log(f"  cache -> {args.cache}")

    # Single patent mode prints the newly cached record. Batch mode writes a
    # compact build result unless --out is omitted.
    if args.patent:
        payload = lookup_payload(con, args.patent)
        if payload:
            print_lookup(payload)
            if args.out:
                args.out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
                log(f"\nJSON -> {args.out}")
    elif args.out:
        args.out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"  build JSON -> {args.out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
