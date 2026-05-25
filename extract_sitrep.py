#!/usr/bin/env python3
"""
SitRep BVD DRC — Table Extraction Pipeline
============================================
Extracts two tables from an INSP DRC situation report PDF using the Anthropic API:
  - new_cases  : the very first table (new / recent cases)
  - cumulative : Table 3 (données cumulatives)

Outputs (written to --output-dir, default ./outputs):
  new_cases_counts.csv
  cumulative_counts.csv
  combined_counts.csv
  raw_extraction.json      (raw Claude JSON, useful for auditing / re-testing)

Usage:
  export ANTHROPIC_API_KEY="sk-ant-..."
  python3 extract_sitrep.py path/to/SitRep.pdf
  python3 extract_sitrep.py path/to/SitRep.pdf --output-dir results/
"""

import argparse
import base64
import json
import json_repair
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import pandas as pd

# ── Configuration ──────────────────────────────────────────────────────────────

MODEL      = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_TOKENS_JSON = 8192   # JSON-only table extraction — well within model limits
MAX_TOKENS_TEXT = 48000  # Full document transcription

# ── Combined linelist schema ─────────────────────────────────────────────────

COMBINED_COLS = [
    "count_start_date", "count_end_date", "count_type",
    "cases_suspect", "cases_probable", "cases_confirmed",
    "deaths_suspected", "deaths_probable", "deaths_confirmed",
    "zone", "province", "sitrep_source", "is_aggregate",
]

# Keywords that identify an alerts/investigation table that must NOT be treated
# as a cumulative case-count table.  Checked against the table_title (lowercase).
_ALERT_TITLE_KEYWORDS = (
    "alertes", "recues", "reçues", "validees", "validées",
    "investiguees", "investiguées", "enquêtées", "notifiees",
    "notifiées", "investigation", "suivi des alertes",
)

FRENCH_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12,
}

# ── Zone name normalisation ───────────────────────────────────────────────────
# Regex to detect aggregate / subtotal rows (zone or province field).
_TOTAL_RE = re.compile(r"\b(total|sous[\s\-]?total)\b", re.IGNORECASE)

# Keys are lowercase; values are the canonical display spelling.
ZONE_NAME_MAP: dict[str, str] = {
    "mungbwalu": "Mongbwalu",
    "bambu":     "Bambu",
}


# Detect rows where multiple zones are packed into a single cell, e.g.
# "Mongbwalu, Bunia, Rwampara (3 ZS)" — these are aggregate summary rows.
_MULTI_ZONE_RE = re.compile(r",|\(\d+\s*ZS\)", re.IGNORECASE)


def _is_aggregate(zone: str, province: str) -> str:
    """Return 'TRUE' if the row is a summary/total row, 'FALSE' otherwise."""
    z = (zone or "").strip()
    p = (province or "").strip()
    if _TOTAL_RE.search(z):
        return "TRUE"
    if not z and _TOTAL_RE.search(p):
        return "TRUE"
    # Multiple zones packed into one cell (e.g. "Mongbwalu, Bunia, Rwampara")
    if _MULTI_ZONE_RE.search(z):
        return "TRUE"
    # Province-level aggregate: zone is empty but province is named.
    # These rows represent province-level summaries (not individual health zones).
    if not z and p:
        return "TRUE"
    return "FALSE"


def normalise_zone(zone: str) -> str:
    """Return the canonical spelling for a health-zone name, else the original."""
    stripped = zone.strip()
    return ZONE_NAME_MAP.get(stripped.lower(), stripped)

# ── Extraction prompts (two focused API calls) ──────────────────────────────

# Call 1: structured JSON extraction only — short, predictable response.
JSON_EXTRACTION_PROMPT = """\
You are an expert epidemiological data extractor processing a French-language \
situation report (Rapport de Situation / SitRep) about MVE (Maladie à Virus \
Ebola or Marburg) in the DRC (République Démocratique du Congo).

Output ONLY a valid JSON object — no markdown fences, no commentary — \
containing EXACTLY two keys. Read directly from the PDF visuals.

TABLE 1 — key "new_cases":
  The table showing the most recent daily case counts (nouvelles données, nouveaux \
cas, or similar). It may be a single summary row, a per-zone breakdown, or a \
pivoted table where health zones appear as columns — if so, TRANSPOSE it so each \
output row represents one zone.
  If the document has no dedicated new-cases table (e.g. it is an alerts or \
administrative report, or there is no section reporting new cases for a single \
day), return "rows": [].

TABLE 2 — key "cumulative":
  The table with cumulative totals (Tableau 3, données cumulatives / cumulées, or \
similar). Apply the same transposition rule if zones are columns.
  CRITICAL — ALERTS TABLE REJECTION: Some documents contain a table tracking alert
  investigations, NOT case counts. Return "rows": [] for the cumulative key if the
  table's actual column headers contain words like: alertes, reçues, validées,
  investiguées, enquêtées, notifiées. Only extract a cumulative table whose column
  headers clearly refer to CASE COUNTS (cas suspects, cas confirmés, décès).
  If no such table exists, return "rows": [].

For BOTH tables, normalise every row to these EXACT keys — use empty string "" for \
any field not present in the source, and preserve zeros as "0":
  "province"        province or region name (empty if not listed per row)
  "zone_de_sante"   health zone / zone de santé name (empty for summary/total rows)
  "cas_suspects"    count of suspected cases
  "cas_probables"   count of probable cases
  "cas_confirmes"   count of confirmed cases
  "deces_suspects"  count of suspected deaths
  "deces_probables" count of probable deaths
  "deces_confirmes" count of confirmed deaths

Also provide for EACH table:
  "table_title"      : exact title string from the document (empty string if none found)
  "reporting_date"   : the 'Date de rapportage' / 'Date du rapport' / 'Date de rédaction'
                       from the document header info-box, in DD/MM/YYYY format.
                       Empty string if no such field exists in the header.
  "period_end_date"  : end date of this table's reporting period in DD/MM/YYYY format.
                       Use this PRIORITY ORDER — stop at the first that applies:
                       1. A date stated directly in or immediately above the table
                          (e.g. column header 'au 20 mai 2026', title 'cumul au ...').
                       2. The 'Date de rapportage' / 'Date du rapport' from the document
                          header — this is when the data was compiled and takes priority
                          over period-range phrases in the body narrative.
                       3. Body-text phrases: 'en date du', 'cumul au', 'au DD mois YYYY',
                          'période du ... au ...'.
                       Empty string ONLY if genuinely absent from the entire document.
  "period_start_date": start date of the reporting period in DD/MM/YYYY format if stated
                       (e.g. from 'du 1er Avril 2026'). Empty string if not stated.
  "notes"            : any footnote or asterisk text below the table (empty string if none)

TEXT FALLBACK FOR ROW FIELDS:
  If a numeric field is absent from the table itself but its value is explicitly and
  unambiguously stated in the body text, Points Saillants, or footnotes for the same
  reporting unit (zone / province / total) AND for the same reporting period (new cases
  for TABLE 1; cumulative totals for TABLE 2), populate that field from the text.
  Do NOT pull cumulative totals into TABLE 1 rows, or daily new-case counts into TABLE 2
  rows. Do NOT infer, estimate, or calculate — only use figures explicitly stated in the
  document for that table's period.

Schema:
{
  "new_cases": {
    "table_title": "...",
    "reporting_date": "DD/MM/YYYY or empty",
    "period_end_date": "DD/MM/YYYY or empty",
    "period_start_date": "DD/MM/YYYY or empty",
    "columns": ["province", "zone_de_sante", "cas_suspects", "cas_probables",
                "cas_confirmes", "deces_suspects", "deces_probables", "deces_confirmes"],
    "rows": [
      {"province": "...", "zone_de_sante": "...", "cas_suspects": "...",
        "cas_probables": "...", "cas_confirmes": "...", "deces_suspects": "...",
        "deces_probables": "...", "deces_confirmes": "..."},
      ...
    ],
    "notes": "..."
  },
  "cumulative": {
    "table_title": "...",
    "reporting_date": "DD/MM/YYYY or empty",
    "period_end_date": "DD/MM/YYYY or empty",
    "period_start_date": "DD/MM/YYYY or empty",
    "columns": ["province", "zone_de_sante", "cas_suspects", "cas_probables",
                "cas_confirmes", "deces_suspects", "deces_probables", "deces_confirmes"],
    "rows": [...],
    "notes": "..."
  }
}
"""

# Call 2: full document transcription for auditing (optional, no JSON).
TEXT_TRANSCRIPTION_PROMPT = """\
Transcribe the complete text of this PDF exactly as it appears on every page. \
Preserve table structure using whitespace or pipe characters to align columns. \
Do not summarise, interpret, or omit any content.
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

def parse_french_date(text: str) -> str:
    """Return first 'DD mois YYYY' date found in text as DD/MM/YYYY, else ''."""
    pattern = r"(\d{1,2})\s+(" + "|".join(FRENCH_MONTHS) + r")\s+(\d{4})"
    m = re.search(pattern, text, re.IGNORECASE)
    if m:
        day   = int(m.group(1))
        month = FRENCH_MONTHS.get(m.group(2).lower().replace("é", "e").replace("û", "u"))
        year  = int(m.group(3))
        if month:
            return datetime(year, month, day).strftime("%d/%m/%Y")
    return ""


def extract_date_from_filename(path: Path) -> str:
    """Parse YYYYMMDD or DD-mois-YYYY from filename stem; return DD/MM/YYYY else ''."""
    # Try compact numeric YYYYMMDD first
    m = re.search(r"(\d{4})(\d{2})(\d{2})", path.stem)
    if m:
        return f"{m.group(3)}/{m.group(2)}/{m.group(1)}"
    # Try French text date (e.g. 18-mai-2026 or 20_mai_2026)
    months_pat = "|".join(FRENCH_MONTHS)
    m2 = re.search(rf"(\d{{1,2}})[-_\s]+({months_pat})[-_\s]+(\d{{4}})", path.stem, re.IGNORECASE)
    if m2:
        month = FRENCH_MONTHS.get(m2.group(2).lower().replace("é", "e").replace("û", "u"))
        if month:
            return datetime(int(m2.group(3)), month, int(m2.group(1))).strftime("%d/%m/%Y")
    return ""


def _nd(value: str) -> str:
    """Return empty string for non-data sentinel values (ND, N, etc.)."""
    return "" if str(value).strip().upper() in ("ND", "N", "N/A", "-", "—") else str(value).strip()


_NUMERIC_FIELDS = (
    "cas_suspects", "cas_probables", "cas_confirmes",
    "deces_suspects", "deces_probables", "deces_confirmes",
)


def _row_has_data(r: dict) -> bool:
    """Return True if at least one case/death field contains a non-empty value."""
    return any(_nd(r.get(f, "")) != "" for f in _NUMERIC_FIELDS)


def build_combined_counts(raw_data: dict, sitrep_date: str, source: str = "") -> pd.DataFrame:
    """
    Merge new_cases and cumulative into a single standardised linelist.
    count_type = 'Nouveaux'  for the first table (new cases, single reporting day)
    count_type = 'Cumules'   for Table 3 (cumulative up to the date in its title)
    Expects normalised keys from TABLE_EXTRACTION_PROMPT.
    """
    # Resolve cumulative date: table title → reporting_date → period_end_date → notes → filename
    cum_end = (
        parse_french_date(raw_data["cumulative"].get("table_title", ""))
        or raw_data["cumulative"].get("reporting_date", "")
        or raw_data["cumulative"].get("period_end_date", "")
        or parse_french_date(raw_data["cumulative"].get("notes", ""))
        or sitrep_date
    )
    cum_start = raw_data["cumulative"].get("period_start_date", "")

    # Resolve new-cases dates: filename → reporting_date → period_end_date → notes
    nc_end = (
        sitrep_date
        or raw_data["new_cases"].get("reporting_date", "")
        or raw_data["new_cases"].get("period_end_date", "")
        or parse_french_date(raw_data["new_cases"].get("notes", ""))
    )
    nc_start = sitrep_date or raw_data["new_cases"].get("period_start_date", "")

    rows = []

    # ── Nouveaux
    for r in raw_data["new_cases"]["rows"]:
        if not _row_has_data(r):
            continue
        rows.append({
            "count_start_date": nc_start,
            "count_end_date":   nc_end,
            "count_type":       "Nouveaux",
            "cases_suspect":    _nd(r.get("cas_suspects", "")),
            "cases_probable":   _nd(r.get("cas_probables", "")),
            "cases_confirmed":  _nd(r.get("cas_confirmes", "")),
            "deaths_suspected": _nd(r.get("deces_suspects", "")),
            "deaths_probable":  _nd(r.get("deces_probables", "")),
            "deaths_confirmed": _nd(r.get("deces_confirmes", "")),
            "zone":             normalise_zone(r.get("zone_de_sante", "")),
            "province":         r.get("province", ""),
            "sitrep_source":    source,
            "is_aggregate":     _is_aggregate(r.get("zone_de_sante", ""), r.get("province", "")),
        })

    # ── Cumules
    for r in raw_data["cumulative"]["rows"]:
        if not _row_has_data(r):
            continue
        rows.append({
            "count_start_date": cum_start,
            "count_end_date":   cum_end,
            "count_type":       "Cumules",
            "cases_suspect":    _nd(r.get("cas_suspects", "")),
            "cases_probable":   _nd(r.get("cas_probables", "")),
            "cases_confirmed":  _nd(r.get("cas_confirmes", "")),
            "deaths_suspected": _nd(r.get("deces_suspects", "")),
            "deaths_probable":  _nd(r.get("deces_probables", "")),
            "deaths_confirmed": _nd(r.get("deces_confirmes", "")),
            "zone":             normalise_zone(r.get("zone_de_sante", "")),
            "province":         r.get("province", ""),
            "sitrep_source":    source,
            "is_aggregate":     _is_aggregate(r.get("zone_de_sante", ""), r.get("province", "")),
        })

    return pd.DataFrame(rows, columns=COMBINED_COLS)


def load_pdf_b64(path: Path) -> str:
    """Read a PDF file and return its base64-encoded content."""
    with open(path, "rb") as fh:
        return base64.standard_b64encode(fh.read()).decode("utf-8")


def clean_json_text(raw: str) -> str:
    """Strip markdown code fences and leading/trailing whitespace."""
    # Remove ```json ... ``` or ``` ... ```
    raw = re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip())
    raw = re.sub(r"\s*```$", "", raw.strip())
    return raw.strip()


def _stream_with_retry(
    client: anthropic.Anthropic,
    prompt: str,
    pdf_b64: str,
    max_tokens: int,
    label: str,
) -> str:
    """
    Send one prompt + PDF to Claude via streaming and return the response text.
    Retries once on any network/API error.
    """
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_b64,
                    },
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    last_exc: Exception | None = None
    for attempt in range(1, 3):
        if attempt > 1:
            print(f"  Retrying {label} (attempt {attempt}) …")
        try:
            with client.messages.stream(
                model=MODEL, max_tokens=max_tokens, temperature=0, messages=messages,
            ) as stream:
                text = stream.get_final_text()
                stop_reason = stream.get_final_message().stop_reason
            if stop_reason == "max_tokens":
                print(f"  WARNING: {label} response truncated (max_tokens).")
            return text
        except Exception as exc:
            last_exc = exc
            print(f"  WARNING: {type(exc).__name__} on {label} attempt {attempt}: {exc}")
    raise ValueError(f"{label} failed after 2 attempts: {last_exc}") from last_exc


def extract_tables(
    client: anthropic.Anthropic, pdf_b64: str, *, include_full_text: bool = True
) -> dict:
    """
    Two-call extraction pipeline.

    Call 1 — JSON tables only: short, focused prompt → small, reliable response.
      Uses MAX_TOKENS_JSON (8192) so truncation is essentially impossible.
    Call 2 — Full transcription (optional): stored in raw_extraction.json for auditing.
      Uses MAX_TOKENS_TEXT (48000). Failures here are non-fatal.

    Splitting the calls eliminates the max_tokens truncation and separator-splitting
    fragility of the previous single-pass approach.
    """
    # ── Call 1: structured JSON ───────────────────────────────────────────────
    print("  Extracting structured tables (JSON) …")
    raw_json = _stream_with_retry(
        client, JSON_EXTRACTION_PROMPT, pdf_b64, MAX_TOKENS_JSON, label="JSON extraction"
    )
    cleaned = clean_json_text(raw_json.strip())
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        print(f"  WARNING: JSON parse error: {exc}. Attempting repair …")
        data = json_repair.loads(cleaned)

    # ── Call 2: full text transcription (optional) ────────────────────────────
    if include_full_text:
        print("  Transcribing full document text …")
        try:
            data["full_text"] = _stream_with_retry(
                client, TEXT_TRANSCRIPTION_PROMPT, pdf_b64, MAX_TOKENS_TEXT,
                label="text transcription",
            )
        except Exception as exc:
            print(f"  WARNING: transcription failed ({exc}). Continuing without full_text.")
            data["full_text"] = ""
    else:
        data["full_text"] = ""

    return data


def coerce_numerics(df: pd.DataFrame) -> pd.DataFrame:
    """
    Try to convert columns to numeric where possible.
    Cells containing only dashes, dots or empty strings become NaN.
    """
    for col in df.columns:
        trial = (
            df[col]
            .astype(str)
            .str.strip()
            .replace({"—": "", "-": "", "–": "", "": None})
        )
        numeric = pd.to_numeric(trial, errors="coerce")
        # Only cast if at least half the non-null values are numeric
        if numeric.notna().sum() >= max(1, df[col].notna().sum() / 2):
            df[col] = numeric
    return df


def build_dataframe(table_data: dict) -> pd.DataFrame:
    rows = table_data.get("rows", [])
    if not rows:
        return pd.DataFrame(columns=table_data.get("columns", []))
    df = pd.DataFrame(rows, columns=table_data.get("columns"))
    df = coerce_numerics(df)
    return df


def save_outputs(
    new_cases_df: pd.DataFrame,
    cumulative_df: pd.DataFrame,
    combined_df: pd.DataFrame,
    raw_data: dict,
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    # ── CSV
    new_cases_df.to_csv(
        output_dir / "new_cases_counts.csv", index=False, encoding="utf-8-sig"
    )
    cumulative_df.to_csv(
        output_dir / "cumulative_counts.csv", index=False, encoding="utf-8-sig"
    )
    combined_df.to_csv(
        output_dir / "combined_counts.csv", index=False, encoding="utf-8-sig"
    )

    # ── Raw JSON (for auditing / re-running tests without an API call)
    with open(output_dir / "raw_extraction.json", "w", encoding="utf-8") as fh:
        json.dump(raw_data, fh, ensure_ascii=False, indent=2)


# ── Entry point ───────────────────────────────────────────────────────────────

def _find_sitrep_pdf(directory: Path):
    """Return the first *SitRep*MVE*RDC*.pdf found in directory, or None."""
    candidates = sorted(directory.glob("*SitRep*MVE*RDC*.pdf"))
    return candidates[0] if candidates else None


def _process_one(
    client: anthropic.Anthropic,
    pdf_path: Path,
    output_dir: Path,
    label: str = "",
) -> tuple:
    """Extract tables from one PDF, save per-file outputs, return (combined_df, raw_data, new_cases_df, cumulative_df)."""
    tag = f"[{label}] " if label else ""
    print(f"{tag}Loading PDF  : {pdf_path.name}  ({pdf_path.stat().st_size / 1024:.0f} KB)")
    pdf_b64 = load_pdf_b64(pdf_path)
    print(f"{tag}Calling Claude ({MODEL}) — visual extraction …")
    data = extract_tables(client, pdf_b64)
    print(f"{tag}Building DataFrames …")
    new_cases_df  = build_dataframe(data["new_cases"])
    # Post-extraction validation: discard cumulative table if it looks like an
    # alerts/investigation table.
    # --- Level 1: table title check + data check ---
    # Reject only when BOTH conditions hold: the title contains alert keywords
    # AND none of the rows contain actual case-count data.  This prevents false
    # rejection of hybrid tables that mention alerts but also carry case counts.
    cum_title_raw = data["cumulative"].get("table_title", "")
    cum_title_lc  = cum_title_raw.lower()
    title_looks_like_alerts = any(kw in cum_title_lc for kw in _ALERT_TITLE_KEYWORDS)
    cum_rows_have_data = any(_row_has_data(r) for r in data["cumulative"].get("rows", []))
    if title_looks_like_alerts and not cum_rows_have_data:
        print(f"{tag}WARNING: cumulative table rejected — title indicates alerts/"
              f"investigation table and rows contain no case data: '{cum_title_raw}'")
        data["cumulative"]["rows"] = []
    # --- Level 2: row-level heuristic (catches tables whose title is neutral) ---
    cumul_rows = data["cumulative"].get("rows", [])
    for row in cumul_rows:
        try:
            prob_str = row.get("cas_probables", "").strip()
            susp_str = row.get("cas_suspects",  "").strip()
            if prob_str.upper() in ("", "ND", "N/A", "-", "—") or susp_str.upper() in ("", "ND", "N/A", "-", "—"):
                continue
            prob = float(prob_str)
            susp = float(susp_str)
            if prob > 0 and susp > 0 and prob > susp * 3:
                print(f"{tag}WARNING: cumulative table rejected — probable ({prob:.0f}) > "
                      f"3× suspect ({susp:.0f}) in zone ‘{row.get('zone_de_sante','')}’. "
                      "Likely an alerts table, not a case-count table.")
                data["cumulative"]["rows"] = []
                break
        except (ValueError, TypeError):
            pass
    # --- Level 3: guard against text-fallback rows when no tables exist ---
    # If cumulative was cleared (alerts document) AND new_cases has no identified
    # table title, any new_cases rows were synthesised purely from body text.
    # These are unreliable; suppress them to avoid spurious Nouveaux rows.
    if not data["cumulative"].get("rows") and not data["new_cases"].get("table_title", ""):
        if data["new_cases"].get("rows"):
            print(f"{tag}WARNING: new_cases rows suppressed — cumulative rejected as "
                  "alerts table and new_cases has no identified table title "
                  "(text-fallback rows discarded).")
        data["new_cases"]["rows"] = []
    cumulative_df = build_dataframe(data["cumulative"])
    sitrep_date   = extract_date_from_filename(pdf_path)
    # Cascade: if filename has no date, try new_cases then cumulative table titles
    if not sitrep_date:
        sitrep_date = parse_french_date(data["new_cases"].get("table_title", ""))
    if not sitrep_date:
        sitrep_date = parse_french_date(data["cumulative"].get("table_title", ""))
    combined_df   = build_combined_counts(data, sitrep_date, source=pdf_path.stem)
    print(f"{tag}Saving outputs → {output_dir.name}/")
    save_outputs(new_cases_df, cumulative_df, combined_df, data, output_dir)
    return combined_df, data, new_cases_df, cumulative_df


def load_processed(path: Path) -> dict:
    """Return the processed-PDFs registry (filename → metadata) or an empty dict."""
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def save_processed(path: Path, processed: dict) -> None:
    """Persist the processed-PDFs registry to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(processed, ensure_ascii=False, indent=2), encoding="utf-8")


def _sort_master(df: pd.DataFrame) -> pd.DataFrame:
    """Sort the master counts table chronologically by count_end_date, then count_type."""
    tmp = df.copy()
    tmp["_sort_date"] = pd.to_datetime(tmp["count_end_date"], format="%d/%m/%Y", errors="coerce")
    tmp["_type_order"] = tmp["count_type"].map({"Nouveaux": 0, "Cumules": 1}).fillna(2).astype(int)
    tmp = tmp.sort_values(
        ["_sort_date", "_type_order", "sitrep_source", "zone"],
        na_position="last",
    )
    return tmp.drop(columns=["_sort_date", "_type_order"]).reset_index(drop=True)


def append_to_master(new_df: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    """Append rows to master_combined_counts.csv; return the full updated DataFrame."""
    master_path = output_dir / "master_combined_counts.csv"
    if master_path.exists():
        existing = pd.read_csv(master_path, dtype=str, encoding="utf-8-sig").fillna("")
        master = pd.concat([existing, new_df.astype(str).fillna("")], ignore_index=True)
    else:
        master = new_df.copy()
    master = _sort_master(master)
    output_dir.mkdir(parents=True, exist_ok=True)
    master.to_csv(master_path, index=False, encoding="utf-8-sig")
    return master



def _run_update(client: anthropic.Anthropic, output_dir: Path, pdf_dir: Path) -> None:
    """Process every PDF in pdf_dir not yet recorded in outputs/processed.json."""
    if not pdf_dir.exists():
        sys.exit(
            f"Error: --pdf-dir '{pdf_dir}' does not exist.\n"
            "  Run fetch_sitreps.py first to download SitRep PDFs."
        )

    processed_path = output_dir / "processed.json"
    processed = load_processed(processed_path)

    all_pdfs = sorted(pdf_dir.glob("*.pdf"))
    new_pdfs = [p for p in all_pdfs if p.name not in processed]

    if not new_pdfs:
        print("No new SitRep PDFs to process — master linelist is up to date.")
        return

    print(f"Update mode: {len(new_pdfs)} new PDF(s) in {pdf_dir.name}/\n")
    all_new = []
    for i, pdf_path in enumerate(new_pdfs, 1):
        per_dir = output_dir / pdf_path.stem
        combined_df, _, _, _ = _process_one(
            client, pdf_path, per_dir, label=f"{i}/{len(new_pdfs)}"
        )
        all_new.append(combined_df)
        processed[pdf_path.name] = {
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "rows_added": len(combined_df),
        }
        print()

    new_rows_df = pd.concat(all_new, ignore_index=True)
    master_df   = append_to_master(new_rows_df, output_dir)

    save_processed(processed_path, processed)

    print("\u2550" * 55)
    print(f"  {len(new_pdfs)} new SitRep(s) processed")
    print(f"  New rows added : {len(new_rows_df)}")
    print(f"  Master total   : {len(master_df)} rows")
    print("\u2550" * 55)
    print(f"\nOutputs written to: {output_dir}/")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Extract new-cases and cumulative tables from one or more "
            "INSP DRC MVE SitRep PDFs."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  ANTHROPIC_API_KEY  or  CLAUDE_API_KEY   Anthropic API key (required)\n"
            "  ANTHROPIC_MODEL                         Model override (default: claude-sonnet-4-6)\n"
        ),
    )
    parser.add_argument(
        "pdf",
        nargs="*",
        metavar="PATH_TO_PDF",
        help=(
            "Path(s) to one or more SitRep PDFs. If omitted (and not using --update), "
            "the script looks for a *SitRep*MVE*RDC*.pdf in the current directory."
        ),
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="outputs",
        metavar="DIR",
        help="Directory for output files (default: ./outputs).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help=(
            "Process all PDFs in --pdf-dir not yet in outputs/processed.json "
            "and append rows to the master linelist."
        ),
    )
    parser.add_argument(
        "--pdf-dir",
        default="pdfs",
        metavar="DIR",
        help="Archived PDF directory used with --update (default: ./pdfs).",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir).expanduser().resolve()

    # ── Resolve API key
    api_key = (
        os.environ.get("ANTHROPIC_API_KEY", "")
        or os.environ.get("CLAUDE_API_KEY", "")
    ).strip()
    if not api_key:
        sys.exit(
            "Error: Neither ANTHROPIC_API_KEY nor CLAUDE_API_KEY is set.\n"
            "  export ANTHROPIC_API_KEY='sk-ant-...'"
        )

    client = anthropic.Anthropic(api_key=api_key, timeout=600.0)

    # ── Update mode: process new PDFs from local archive
    if args.update:
        _run_update(client, output_dir, Path(args.pdf_dir).expanduser().resolve())
        return

    # ── Normal mode: explicit PDF path(s) or auto-detect
    if args.pdf:
        pdf_paths = [Path(p).expanduser().resolve() for p in args.pdf]
    else:
        found = _find_sitrep_pdf(Path.cwd())
        if found is None:
            sys.exit(
                "Error: no PDF path given and no *SitRep*MVE*RDC*.pdf found in the "
                "current directory.\n  Usage: python3 extract_sitrep.py path/to/SitRep.pdf"
            )
        pdf_paths = [found]

    for p in pdf_paths:
        if not p.exists():
            sys.exit(f"Error: PDF not found at {p}")

    n = len(pdf_paths)

    if n == 1:
        # ── Single-PDF: backward-compatible behaviour
        combined_df, data, new_cases_df, cumulative_df = _process_one(
            client, pdf_paths[0], output_dir
        )
        print()
        print("═" * 55)
        nc_title  = data["new_cases"].get("table_title", "(no title)")
        cum_title = data["cumulative"].get("table_title", "(no title)")
        print(f"  Nouveaux cas  [{nc_title}]")
        print(f"    {len(new_cases_df)} rows  ×  {len(new_cases_df.columns)} columns")
        print()
        print(f"  Cumulatif     [{cum_title}]")
        print(f"    {len(cumulative_df)} rows  ×  {len(cumulative_df.columns)} columns")
        print()
        print(f"  Combined linelist: {len(combined_df)} rows  (Nouveaux + Cumules)")
        print("═" * 55)
        print(f"\nOutputs written to: {output_dir}/")

    else:
        # ── Batch mode: each PDF → own sub-directory, then build master
        print(f"Batch mode: {n} PDFs → {output_dir}/\n")
        all_combined = []
        for i, pdf_path in enumerate(pdf_paths, 1):
            per_dir = output_dir / pdf_path.stem
            combined_df, _, _, _ = _process_one(
                client, pdf_path, per_dir, label=f"{i}/{n}"
            )
            all_combined.append(combined_df)
            print()

        print("Building master linelist …")
        master_df = _sort_master(pd.concat(all_combined, ignore_index=True))

        master_csv = output_dir / "master_combined_linelist.csv"
        master_df.to_csv(master_csv, index=False, encoding="utf-8-sig")

        print()
        print("═" * 55)
        print(f"  {n} SitReps processed")
        print(f"  Master rows : {len(master_df)}")
        print(f"    Nouveaux  : {(master_df['count_type'] == 'Nouveaux').sum()}")
        print(f"    Cumules   : {(master_df['count_type'] == 'Cumules').sum()}")
        print("═" * 55)
        print(f"\nMaster output written to: {output_dir}/{master_csv.name}")


if __name__ == "__main__":
    main()
