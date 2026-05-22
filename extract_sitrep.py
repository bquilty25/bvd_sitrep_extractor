#!/usr/bin/env python3
"""
SitRep BVD DRC — Table Extraction Pipeline
============================================
Extracts two tables from an INSP DRC situation report PDF using the Anthropic API:
  - new_cases  : the very first table (new / recent cases)
  - cumulative : Table 3 (données cumulatives)

Outputs (written to --output-dir, default ./outputs):
  new_cases_linelist.csv
  cumulative_linelist.csv
  combined_linelist.csv
  sitrep_extraction.xlsx   (all three as separate sheets)
  raw_extraction.json      (raw Claude JSON, useful for auditing / re-testing)

Usage:
  export ANTHROPIC_API_KEY="sk-ant-..."
  python3 extract_sitrep.py path/to/SitRep.pdf
  python3 extract_sitrep.py path/to/SitRep.pdf --output-dir results/
"""

import argparse
import base64
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import pandas as pd

# ── Configuration ──────────────────────────────────────────────────────────────

MODEL           = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_TOKENS_TEXT = 32000  # Step 1: full-text extraction (can be long)
MAX_TOKENS      = 16000  # Step 2: structured table extraction

# ── Combined linelist schema ─────────────────────────────────────────────────

COMBINED_COLS = [
    "count_start_date", "count_end_date", "count_type",
    "cases_suspect", "cases_probable", "cases_confirmed",
    "deaths_suspected", "deaths_probable", "deaths_confirmed",
    "zone", "province",
]

FRENCH_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4,
    "mai": 5, "juin": 6, "juillet": 7, "août": 8, "aout": 8,
    "septembre": 9, "octobre": 10, "novembre": 11,
    "décembre": 12, "decembre": 12,
}

# ── Extraction prompts ────────────────────────────────────────────────────────

# Step 1: extract raw text from the PDF
TEXT_EXTRACTION_PROMPT = """\
Extract all text from this PDF document exactly as it appears on every page. \
Preserve the structure of tables using whitespace or pipe characters to align columns. \
Do not summarise, interpret, or omit any content. \
Output the complete raw textual content of the document."""

# Step 2: parse structured table data from the extracted text
TABLE_EXTRACTION_PROMPT = """\
You are an expert epidemiological data extractor. The following text has been \
extracted from a French-language situation report (Rapport de Situation / SitRep) \
about MVE (Maladie à Virus Ebola or Marburg) in the DRC \
(République Démocratique du Congo).

Find and normalise EXACTLY two tables, returning them as a single valid JSON object.

TABLE 1 — key "new_cases":
  The table showing the most recent daily case counts (nouvelles données, nouveaux \
cas, tableau de bord, or similar). It may be a single summary row, a per-zone \
breakdown, or a pivoted table where health zones appear as columns — if so, \
TRANSPOSE it so each output row represents one zone.

TABLE 2 — key "cumulative":
  The table with cumulative totals (Tableau 3, données cumulatives / cumulées, or \
similar). Apply the same transposition rule if zones are columns.

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

Also provide:
  "table_title" : exact title string from the document (empty string if none found)
  "notes"       : any footnote or asterisk text below the table (empty string if none)

Return ONLY the JSON — no markdown fences, no commentary, nothing else.

Schema:
{{
  "new_cases": {{
    "table_title": "...",
    "columns": ["province", "zone_de_sante", "cas_suspects", "cas_probables",
                "cas_confirmes", "deces_suspects", "deces_probables", "deces_confirmes"],
    "rows": [
      {{"province": "...", "zone_de_sante": "...", "cas_suspects": "...",
        "cas_probables": "...", "cas_confirmes": "...", "deces_suspects": "...",
        "deces_probables": "...", "deces_confirmes": "..."}},
      ...
    ],
    "notes": "..."
  }},
  "cumulative": {{
    "table_title": "...",
    "columns": ["province", "zone_de_sante", "cas_suspects", "cas_probables",
                "cas_confirmes", "deces_suspects", "deces_probables", "deces_confirmes"],
    "rows": [...],
    "notes": "..."
  }}
}}
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


def build_combined_linelist(raw_data: dict, sitrep_date: str) -> pd.DataFrame:
    """
    Merge new_cases and cumulative into a single standardised linelist.
    count_type = 'Nouveaux'  for the first table (new cases, single reporting day)
    count_type = 'Cumules'   for Table 3 (cumulative up to the date in its title)
    Expects normalised keys from TABLE_EXTRACTION_PROMPT.
    """
    cum_end = (parse_french_date(raw_data["cumulative"].get("table_title", ""))
               or sitrep_date)

    rows = []

    # ── Nouveaux
    for r in raw_data["new_cases"]["rows"]:
        rows.append({
            "count_start_date": sitrep_date,
            "count_end_date":   sitrep_date,
            "count_type":       "Nouveaux",
            "cases_suspect":    _nd(r.get("cas_suspects", "")),
            "cases_probable":   _nd(r.get("cas_probables", "")),
            "cases_confirmed":  _nd(r.get("cas_confirmes", "")),
            "deaths_suspected": _nd(r.get("deces_suspects", "")),
            "deaths_probable":  _nd(r.get("deces_probables", "")),
            "deaths_confirmed": _nd(r.get("deces_confirmes", "")),
            "zone":             r.get("zone_de_sante", ""),
            "province":         r.get("province", ""),
        })

    # ── Cumules
    for r in raw_data["cumulative"]["rows"]:
        rows.append({
            "count_start_date": "",
            "count_end_date":   cum_end,
            "count_type":       "Cumules",
            "cases_suspect":    _nd(r.get("cas_suspects", "")),
            "cases_probable":   _nd(r.get("cas_probables", "")),
            "cases_confirmed":  _nd(r.get("cas_confirmes", "")),
            "deaths_suspected": _nd(r.get("deces_suspects", "")),
            "deaths_probable":  _nd(r.get("deces_probables", "")),
            "deaths_confirmed": _nd(r.get("deces_confirmes", "")),
            "zone":             r.get("zone_de_sante", ""),
            "province":         r.get("province", ""),
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


def _extract_full_text(client: anthropic.Anthropic, pdf_b64: str) -> str:
    """Step 1: extract raw text from the PDF via Claude's document vision."""
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS_TEXT,
        messages=[
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
                    {"type": "text", "text": TEXT_EXTRACTION_PROMPT},
                ],
            }
        ],
    ) as stream:
        return stream.get_final_text()


def _extract_tables_from_text(client: anthropic.Anthropic, text: str) -> dict:
    """Step 2: parse structured table JSON from plain extracted text."""
    prompt = TABLE_EXTRACTION_PROMPT + "\n\n---\nDOCUMENT TEXT:\n\n" + text
    with client.messages.stream(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        raw_text = stream.get_final_text()
    cleaned = clean_json_text(raw_text)
    return json.loads(cleaned)


def extract_tables(client: anthropic.Anthropic, pdf_b64: str) -> dict:
    """
    Two-step extraction pipeline:
      1. Send the PDF to Claude to extract full document text.
      2. Send that text back to Claude to parse the two epidemiological tables.
    Returns the parsed dict with an additional 'full_text' key for auditing.
    """
    print("  Step 1/2 — Extracting full document text …")
    full_text = _extract_full_text(client, pdf_b64)
    print("  Step 2/2 — Parsing epidemiological tables from text …")
    data = _extract_tables_from_text(client, full_text)
    data["full_text"] = full_text
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
        output_dir / "new_cases_linelist.csv", index=False, encoding="utf-8-sig"
    )
    cumulative_df.to_csv(
        output_dir / "cumulative_linelist.csv", index=False, encoding="utf-8-sig"
    )
    combined_df.to_csv(
        output_dir / "combined_linelist.csv", index=False, encoding="utf-8-sig"
    )

    # ── Excel (three sheets)
    with pd.ExcelWriter(
        output_dir / "sitrep_extraction.xlsx", engine="openpyxl"
    ) as writer:
        new_cases_df.to_excel(writer, sheet_name="Nouveaux cas", index=False)
        cumulative_df.to_excel(writer, sheet_name="Cumulatif (Tableau 3)", index=False)
        combined_df.to_excel(writer, sheet_name="Combined linelist", index=False)

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
    print(f"{tag}Calling Claude ({MODEL}) — 2-step extraction …")
    data = extract_tables(client, pdf_b64)
    print(f"{tag}Building DataFrames …")
    new_cases_df  = build_dataframe(data["new_cases"])
    cumulative_df = build_dataframe(data["cumulative"])
    sitrep_date   = extract_date_from_filename(pdf_path)
    # Cascade: if filename has no date, try new_cases then cumulative table titles
    if not sitrep_date:
        sitrep_date = parse_french_date(data["new_cases"].get("table_title", ""))
    if not sitrep_date:
        sitrep_date = parse_french_date(data["cumulative"].get("table_title", ""))
    combined_df   = build_combined_linelist(data, sitrep_date)
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


def append_to_master(new_df: pd.DataFrame, output_dir: Path) -> pd.DataFrame:
    """Append rows to master_combined_linelist.csv; return the full updated DataFrame."""
    master_path = output_dir / "master_combined_linelist.csv"
    if master_path.exists():
        existing = pd.read_csv(master_path, dtype=str, encoding="utf-8-sig").fillna("")
        master = pd.concat([existing, new_df.astype(str).fillna("")], ignore_index=True)
    else:
        master = new_df.copy()
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

    master_xlsx = output_dir / "master_sitrep_extraction.xlsx"
    with pd.ExcelWriter(master_xlsx, engine="openpyxl") as writer:
        master_df.to_excel(writer, sheet_name="Master combined", index=False)

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

    client = anthropic.Anthropic(api_key=api_key)

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
        master_df = pd.concat(all_combined, ignore_index=True)

        master_csv  = output_dir / "master_combined_linelist.csv"
        master_xlsx = output_dir / "master_sitrep_extraction.xlsx"
        master_df.to_csv(master_csv, index=False, encoding="utf-8-sig")
        with pd.ExcelWriter(master_xlsx, engine="openpyxl") as writer:
            master_df.to_excel(writer, sheet_name="Master combined", index=False)

        print()
        print("═" * 55)
        print(f"  {n} SitReps processed")
        print(f"  Master rows : {len(master_df)}")
        print(f"    Nouveaux  : {(master_df['count_type'] == 'Nouveaux').sum()}")
        print(f"    Cumules   : {(master_df['count_type'] == 'Cumules').sum()}")
        print("═" * 55)
        print(f"\nMaster outputs written to: {output_dir}/")
        print(f"  {master_csv.name}")
        print(f"  {master_xlsx.name}")


if __name__ == "__main__":
    main()
