# SitRep BVD DRC — Table Extraction Pipeline

> **AI-assisted development** — This pipeline and the accompanying report were developed with the assistance of [GitHub Copilot](https://github.com/features/copilot) (Microsoft) and the [Claude API](https://docs.anthropic.com/) (Anthropic, `claude-sonnet-4-6`). The extraction pipeline, report generation, and analysis code were primarily written by AI tools under human review.

Automatically extracts epidemiological data from INSP DRC Ebola situation report PDFs using Claude's visual PDF understanding.

Two tables are extracted from each SitRep and written to three output files:

| Source | Description |
|---|---|
| First table | New cases for the reporting day |
| Tableau III | Cumulative cases and deaths by health zone |
| Combined | Both tables merged into a single standardised counts table |

The pipeline supports one-off extraction of a single PDF, batch processing of multiple PDFs, and a daily update mode that automatically fetches new SitReps from the INSP website and appends only new data to a growing master counts table.

---

## Prerequisites

- Python 3.10 or later
- An [Anthropic API key](https://console.anthropic.com/) (billed per token — a single SitRep PDF costs roughly $0.05–0.10 USD)

---

## Installation

**1. Clone or download this repository**

```bash
git clone <repo-url>
cd bvd_sitrep_extractor
```

**2. Create and activate a virtual environment**

```bash
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows
```

**3. Install dependencies**

```bash
pip install -r requirements.txt
```

**4. Set up your API key**

```bash
cp .env.example .env
```

Open `.env` and replace `sk-ant-...` with your actual Anthropic API key. This file is gitignored and will never be committed.

---

## Usage

### Daily update (recommended)

Run these two commands each day. They can be chained, scheduled with cron, or run manually.

```bash
source .env

# 1. Check INSP website for new PDFs and archive any that are new
python3 fetch_sitreps.py

# 2. Extract only the newly downloaded PDFs and append to the master linelist
python3 extract_sitrep.py --update
```

`fetch_sitreps.py` scrapes `insp.cd/ebola/` and `insp.cd/category/sitrep/` for PDF links, downloads new ones to `pdfs/`, and records them in `pdfs/manifest.json`. PDFs are kept permanently as a local archive in case they are removed from the website.

`extract_sitrep.py --update` reads `pdfs/`, skips any file already recorded in `outputs/processed.json`, extracts the rest, and appends their rows to `master_combined_counts.csv`. Each new SitRep's verbatim tables also land in `outputs/<pdf_stem>/`.

**Cron example** (runs daily at 08:00):

```
0 8 * * * cd /path/to/bvd_sitrep_extractor && source .env && python3 fetch_sitreps.py && python3 extract_sitrep.py --update
```

---

### One-off extraction

Extract a single PDF directly:

```bash
source .env
python3 extract_sitrep.py path/to/SitRep.pdf
```

If the filename follows the standard INSP convention (`*SitRep*MVE*RDC*.pdf`), the path can be omitted and the script will auto-detect it:

```bash
python3 extract_sitrep.py
```

Use `--output-dir` to write elsewhere:

```bash
python3 extract_sitrep.py SitRep.pdf --output-dir results/2026-05-20/
```

### Batch extraction

Pass multiple PDFs to process them all in one run and produce a combined master file:

```bash
python3 extract_sitrep.py SitRep_001.pdf SitRep_002.pdf SitRep_006.pdf
```

Each PDF's verbatim outputs go into `outputs/<pdf_stem>/`. A `master_combined_counts.csv` is written at the `outputs/` root.

---

### Full usage reference

```
python3 fetch_sitreps.py [--pdf-dir DIR] [--pages URL [URL ...]] [--since YYYY-MM]

python3 extract_sitrep.py [PATH_TO_PDF ...] [--output-dir DIR]
                          [--update] [--pdf-dir DIR]

fetch_sitreps.py options:
  --pdf-dir DIR       Local archive for downloaded PDFs (default: ./pdfs)
  --pages URL ...     Pages to scrape (default: insp.cd/ebola/ + category/sitrep/)
  --since YYYY-MM     Only download PDFs uploaded in this month or later (default: 2026-05)

extract_sitrep.py options:
  --output-dir, -o    Output directory (default: ./outputs)
  --update            Process new PDFs from --pdf-dir, append to master
  --pdf-dir DIR       PDF archive used with --update (default: ./pdfs)
```

---

## Outputs

### Per-SitRep files

Written to `outputs/` (single PDF) or `outputs/<pdf_stem>/` (batch / update mode).

| File | Contents |
|---|---|
| `new_cases_counts.csv` | New-cases table extracted verbatim from the PDF |
| `cumulative_counts.csv` | Cumulative table (Tableau III) extracted verbatim |
| `combined_counts.csv` | Both tables in a single standardised 13-column counts table |
| `raw_extraction.json` | Raw JSON returned by Claude — useful for auditing and re-running tests |

### Master files (batch / update mode)

Written to `outputs/`.

| File | Contents |
|---|---|
| `master_combined_counts.csv` | All SitReps concatenated into one standardised counts table, sorted chronologically |
| `processed.json` | Registry of processed PDF filenames and timestamps |

### HTML situation report

A Quarto report (`scripts/sitrep_report.qmd`) reads `master_combined_counts.csv` and renders an HTML report with epidemic curves, zone-level breakdowns, and cross-source validation. Render it from the project root:

```bash
quarto render scripts/sitrep_report.qmd --output-dir outputs
```

Output: `outputs/sitrep_report.html` (self-contained). All figures are also saved as PNGs to `outputs/plots/`.

### PDF archive

Downloaded PDFs are stored in `pdfs/` (gitignored). A `pdfs/manifest.json` records each URL, filename, download timestamp, and file size.

### Combined counts table columns

| Column | Description |
|---|---|
| `count_start_date` | Start of reporting period (DD/MM/YYYY). Blank for cumulative rows — outbreak start date is not stated in the PDF |
| `count_end_date` | End of reporting period (DD/MM/YYYY) |
| `count_type` | `Nouveaux` (new cases) or `Cumules` (cumulative) |
| `cases_suspect` | Suspected cases |
| `cases_probable` | Probable cases |
| `cases_confirmed` | Confirmed cases |
| `deaths_suspected` | Deaths among suspected cases |
| `deaths_probable` | Deaths among probable cases |
| `deaths_confirmed` | Deaths among confirmed cases |
| `zone` | Health zone (*zone de santé*), normalised to a canonical spelling |
| `province` | Province |
| `sitrep_source` | Stem of the source PDF filename — used to trace each row back to its document |

> Cells that appear as `ND` (Non Disponible) in the source PDF are mapped to blank in the combined counts table. Subtotal and total rows are retained as-is.

---

## Running the tests

The test suite has three layers: pure unit tests (no API needed), JSON schema tests, and output data tests.

```bash
pytest tests/ -v
```

Unit tests run without an Anthropic API call. Schema and data tests require the outputs to exist — run the extraction at least once first.

Expected result: **44 passed** (unit tests only — schema and data tests are skipped until the pipeline has been run at least once against a real PDF).

---

## Configuration

| Variable | Required | Default | Description |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | Anthropic API key |
| `CLAUDE_API_KEY` | Alternative | — | Accepted as a fallback if `ANTHROPIC_API_KEY` is not set |
| `ANTHROPIC_MODEL` | No | `claude-sonnet-4-6` | Override the Claude model |

---

## How it works

`fetch_sitreps.py` scrapes INSP web pages for `wp-content/uploads/*.pdf` links, filters for MVE/SitRep-relevant filenames, and downloads new ones to the local archive.

`extract_sitrep.py` sends each PDF to Claude as a base64-encoded `document` block. Claude processes each page as both a rendered image and extracted text simultaneously — this is what allows it to correctly read visually formatted tables, merged cells, and footnotes that plain text extraction tools would mangle. Claude returns a structured JSON object with both tables, which is converted to pandas DataFrames and mapped to the standardised schema.

The `--update` mode compares the PDF archive against `processed.json` to extract only new files, then appends their rows to the master counts table rather than overwriting it.

---

## Adapting to a new SitRep

Each SitRep may use slightly different column names. If the combined counts table has blank columns that should have data:

1. Open `outputs/<pdf_stem>/raw_extraction.json` and check the exact column names Claude found.
2. Update the column name mappings in `build_combined_counts()` in [extract_sitrep.py](extract_sitrep.py).
3. Re-run `pytest tests/` — the `TestCombinedContent` tests will catch any remaining gaps.
