#!/usr/bin/env python3
"""
export_inrb_format.py
=====================
Transforms the master output CSVs produced by extract_sitrep.py into
per-metric CSV files that match the INRB-UMIE / Ebola_DRC_2026 schema:

    nom, date, <metric>

where:
  - nom   = health zone name (or "National" for aggregate files)
  - date  = ISO 8601 date (YYYY-MM-DD)
  - <metric> = one epidemiological variable per file

Inputs (relative to project root):
    outputs/master_combined_counts.csv
    outputs/master_response_counts.csv   (optional)
    outputs/master_poe_counts.csv        (optional)
    outputs/sitreps/*/raw_extraction.json

Outputs:
    data/raw/{sitrep_name}.json          — raw extraction JSON copies
    data/processed/insp_sitrep__*.csv    — INRB-format per-metric files (20 files)

Usage:
    python3 scripts/export_inrb_format.py
    python3 scripts/export_inrb_format.py --output-dir outputs --data-dir data
"""

import argparse
import shutil
from pathlib import Path

import pandas as pd

# ── Project root ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent

# ── Default I/O directories ─────────────────────────────────────────────────────
_DEFAULT_OUTPUT_DIR = ROOT / "outputs"
_DEFAULT_DATA_RAW   = ROOT / "data" / "raw"
_DEFAULT_DATA_PROC  = ROOT / "data" / "processed"

# ── Zone names treated as national aggregates ───────────────────────────────────
_NATIONAL_ZONE_NAMES = frozenset({
    "", "total", "total général", "total general",
    "total general (ituri + nk)", "national",
})

# ── Metric mappings ─────────────────────────────────────────────────────────────
#   Each tuple: (source_column, output_filename, output_metric_column)

ZONE_NOUVEAUX: list[tuple[str, str, str]] = [
    ("cases_suspect",    "insp_sitrep__new_suspected_cases__daily.csv",    "new_suspected_cases"),
    ("cases_confirmed",  "insp_sitrep__new_confirmed_cases__daily.csv",    "new_confirmed_cases"),
    ("deaths_suspected", "insp_sitrep__new_suspected_deaths__daily.csv",   "new_suspected_deaths"),
    ("contacts",         "insp_sitrep__new_contacts_listed__daily.csv",    "new_contacts_listed"),
]

ZONE_CUMULES: list[tuple[str, str, str]] = [
    ("cases_suspect",    "insp_sitrep__cumulative_suspected_cases__daily.csv",  "cumulative_suspected_cases"),
    ("cases_confirmed",  "insp_sitrep__cumulative_confirmed_cases__daily.csv",  "cumulative_confirmed_cases"),
    ("deaths_suspected", "insp_sitrep__cumulative_suspected_deaths__daily.csv", "cumulative_suspected_deaths"),
    ("deaths_confirmed", "insp_sitrep__cumulative_confirmed_deaths__daily.csv", "cumulative_confirmed_deaths"),
    ("contacts",         "insp_sitrep__cumulative_contacts_traced__daily.csv",  "cumulative_contacts_traced"),
]

NATIONAL_CUMULES: list[tuple[str, str, str]] = [
    ("cases_suspect",    "insp_sitrep__national_cumulative_suspected_cases__daily.csv",   "national_cumulative_suspected_cases"),
    ("cases_confirmed",  "insp_sitrep__national_cumulative_confirmed_cases__daily.csv",   "national_cumulative_confirmed_cases"),
    ("deaths_suspected", "insp_sitrep__national_cumulative_suspected_deaths__daily.csv",  "national_cumulative_suspected_deaths"),
    ("deaths_confirmed", "insp_sitrep__national_cumulative_confirmed_deaths__daily.csv",  "national_cumulative_confirmed_deaths"),
]

#   Response: (source_column, output_metric_column, output_filename)
RESPONSE_METRICS: list[tuple[str, str, str]] = [
    ("total_isolated",      "hospitalised",        "insp_sitrep__hospitalised__daily.csv"),
    ("in_bed_previous_day", "in_bed_previous_day", "insp_sitrep__in_bed_previous_day__daily.csv"),
    ("total_admissions",    "new_all_admissions",  "insp_sitrep__new_hosp_admissions__daily.csv"),
]

#   PoE: (source_column, output_metric_column, output_filename)
POE_METRICS: list[tuple[str, str, str]] = [
    ("total_screened",    "total_poe_screened",    "insp_sitrep__total_poe_screened__daily.csv"),
    ("total_passed",      "total_poe_passed",      "insp_sitrep__total_poe_passed__daily.csv"),
    ("total_handwashing", "total_poe_hand_washing", "insp_sitrep__total_poe_hand_washing__daily.csv"),
    ("total_sensitised",  "total_poe_sanitised",   "insp_sitrep__total_poe_sanitised__daily.csv"),
]


# ── Helpers ─────────────────────────────────────────────────────────────────────

def _parse_date(s: str) -> str:
    """Convert DD/MM/YYYY (or any parseable date string) to ISO YYYY-MM-DD."""
    if not s or str(s).strip() == "":
        return ""
    try:
        return pd.to_datetime(s, format="%d/%m/%Y").strftime("%Y-%m-%d")
    except Exception:
        try:
            return pd.to_datetime(s).strftime("%Y-%m-%d")
        except Exception:
            return str(s).strip()


def _to_nd(val: object) -> object:
    """Return 'ND' for empty / NaN values; otherwise return the value unchanged."""
    if pd.isna(val) or str(val).strip() == "":
        return "ND"
    return val


def _write_metric(
    nom: pd.Series,
    date: pd.Series,
    metric: pd.Series,
    metric_name: str,
    outpath: Path,
) -> None:
    """Write a three-column INRB-format CSV (nom, date, metric_name), sorted by date then nom."""
    df = pd.DataFrame({
        "nom":      nom.values,
        "date":     date.values,
        metric_name: metric.values,
    })
    # Drop rows where nom is empty (malformed extraction rows)
    df = df[df["nom"].str.strip() != ""]
    df = df.sort_values(["date", "nom"]).reset_index(drop=True)
    df.to_csv(outpath, index=False, encoding="utf-8")
    print(f"  {outpath.name:65s} {len(df):>4} rows")


# ── Main export function ─────────────────────────────────────────────────────────

def export_inrb_format(
    output_dir: Path = _DEFAULT_OUTPUT_DIR,
    data_raw: Path   = _DEFAULT_DATA_RAW,
    data_proc: Path  = _DEFAULT_DATA_PROC,
) -> None:
    """Export master CSVs into INRB-UMIE per-metric format.

    Parameters
    ----------
    output_dir : Path
        Directory containing master_combined_counts.csv, master_response_counts.csv,
        master_poe_counts.csv, and the sitreps/ subdirectory.
    data_raw : Path
        Destination for raw JSON extraction copies.
    data_proc : Path
        Destination for INRB-format per-metric CSV files.
    """
    data_raw.mkdir(parents=True, exist_ok=True)
    data_proc.mkdir(parents=True, exist_ok=True)

    print("\nExporting INRB-format data …")
    print(f"  → {data_proc}/\n")

    # ── 1. Copy raw extraction JSONs ─────────────────────────────────────────
    sitreps_dir = output_dir / "sitreps"
    n_raw = 0
    if sitreps_dir.exists():
        for sitrep_dir in sorted(sitreps_dir.iterdir()):
            if not sitrep_dir.is_dir():
                continue
            json_src = sitrep_dir / "raw_extraction.json"
            if json_src.exists():
                shutil.copy2(json_src, data_raw / f"{sitrep_dir.name}.json")
                n_raw += 1
    print(f"  data/raw/  ← {n_raw} raw JSON(s) copied\n")

    # ── 2. Case / death / contacts — from master_combined_counts.csv ─────────
    combined_path = output_dir / "master_combined_counts.csv"
    if not combined_path.exists():
        print(f"  WARNING: {combined_path} not found — skipping case/death/contacts export.")
    else:
        df = pd.read_csv(combined_path, dtype=str, encoding="utf-8-sig").fillna("")

        # Normalise boolean flag
        is_agg = df["is_aggregate"].str.strip().str.upper() == "TRUE"

        # ── Zone-level rows (named health zones only)
        zone_mask = (
            ~is_agg
            & df["zone"].str.strip().ne("")
        )
        zone_df = df[zone_mask].copy()
        zone_df["nom"]  = zone_df["zone"].str.strip()
        zone_df["date"] = zone_df["count_end_date"].apply(_parse_date)

        # ── National aggregate rows: is_aggregate=TRUE, province empty, zone is a total label
        nat_mask = (
            is_agg
            & df["zone"].str.strip().str.lower().isin(_NATIONAL_ZONE_NAMES)
            & (df["province"].str.strip() == "")
        )
        nat_df = df[nat_mask].copy()
        nat_df["nom"]  = "National"
        nat_df["date"] = nat_df["count_end_date"].apply(_parse_date)

        # Per date, keep the row with the most non-empty metric values (most complete)
        _fill_cols = ["cases_suspect", "cases_confirmed", "deaths_suspected", "deaths_confirmed"]
        nat_df["_nfill"] = nat_df[_fill_cols].apply(
            lambda c: c.str.strip().ne(""), axis=1
        ).sum(axis=1)
        nat_df = (
            nat_df
            .sort_values(["date", "count_type", "_nfill"], ascending=[True, True, False])
            .drop_duplicates(subset=["date", "count_type"], keep="first")
            .drop(columns=["_nfill"])
        )

        # Nouveaux — zone-level
        nov_df = zone_df[zone_df["count_type"] == "Nouveaux"]
        for src_col, filename, metric_col in ZONE_NOUVEAUX:
            vals = nov_df[src_col].apply(_to_nd)
            _write_metric(nov_df["nom"], nov_df["date"], vals, metric_col, data_proc / filename)

        # Cumules — zone-level
        cum_df = zone_df[zone_df["count_type"] == "Cumules"]
        for src_col, filename, metric_col in ZONE_CUMULES:
            vals = cum_df[src_col].apply(_to_nd)
            _write_metric(cum_df["nom"], cum_df["date"], vals, metric_col, data_proc / filename)

        # Cumules — national
        nat_cum = nat_df[nat_df["count_type"] == "Cumules"]
        for src_col, filename, metric_col in NATIONAL_CUMULES:
            vals = nat_cum[src_col].apply(_to_nd)
            _write_metric(nat_cum["nom"], nat_cum["date"], vals, metric_col, data_proc / filename)

    # ── 3. Response metrics — from master_response_counts.csv ────────────────
    resp_path = output_dir / "master_response_counts.csv"
    if not resp_path.exists():
        print(f"\n  WARNING: {resp_path} not found — skipping response export.")
    else:
        resp = pd.read_csv(resp_path, dtype=str, encoding="utf-8-sig").fillna("")
        resp["nom"]  = resp["zone"].str.strip()
        resp["date"] = resp["date"].apply(_parse_date)
        for src_col, metric_col, filename in RESPONSE_METRICS:
            if src_col in resp.columns:
                vals = resp[src_col].apply(_to_nd)
                _write_metric(resp["nom"], resp["date"], vals, metric_col, data_proc / filename)

    # ── 4. PoE metrics — from master_poe_counts.csv ───────────────────────────
    poe_path = output_dir / "master_poe_counts.csv"
    if not poe_path.exists():
        print(f"\n  WARNING: {poe_path} not found — skipping PoE export.")
    else:
        poe = pd.read_csv(poe_path, dtype=str, encoding="utf-8-sig").fillna("")
        poe["nom"]         = "National"
        poe["date_parsed"] = poe["date"].apply(_parse_date)
        for src_col, metric_col, filename in POE_METRICS:
            if src_col in poe.columns:
                vals = poe[src_col].apply(_to_nd)
                _write_metric(poe["nom"], poe["date_parsed"], vals, metric_col, data_proc / filename)

    print(f"\nDone. INRB-format CSVs written to: {data_proc}/")


# ── CLI entry point ──────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export master CSVs into INRB-UMIE per-metric format."
    )
    parser.add_argument(
        "--output-dir",
        default=str(_DEFAULT_OUTPUT_DIR),
        help=f"Directory containing master CSVs and sitreps/ (default: {_DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--data-dir",
        default=str(ROOT / "data"),
        help=f"Root of data/ directory; raw/ and processed/ are created here (default: {ROOT / 'data'})",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    data_root = Path(args.data_dir).expanduser().resolve()
    export_inrb_format(
        output_dir=Path(args.output_dir).expanduser().resolve(),
        data_raw=data_root / "raw",
        data_proc=data_root / "processed",
    )
