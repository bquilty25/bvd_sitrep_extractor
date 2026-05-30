---
name: sitrep-extract
description: "Extract epidemiological tables from MVE SitRep PDFs using Claude Vision API. Use when: running extraction on new PDFs; Claude API errors (auth, rate limit, timeout, JSON parse failure); understanding extraction output files; re-extracting a specific PDF; diagnosing missing, wrong, or rejected table data; data/processed/processed.json or master CSVs are out of sync."
---

# SitRep Extract

Calls the Claude Vision API to extract case count tables from MVE SitRep PDFs, producing per-sitrep CSVs and appending to master linelists.

## When to Use

- After `sitrep-fetch` reports new PDFs downloaded
- To re-extract a specific PDF after fixing a parsing issue
- Diagnosing Claude API errors or output validation warnings

## Commands

**Update mode — process all new PDFs in the archive:**

```bash
cd /Users/billyquilty/Documents/Work/bvd_sitrep_extractor
set -a && source .env && set +a
python3 scripts/extract_sitrep.py --update
```

**Single PDF (force re-extract, even if already in `processed.json`):**

```bash
python3 scripts/extract_sitrep.py data/raw/<name>/MVE_SitRep_NNN_YYYY-MM-DD.pdf
```

## Expected Output

```
══════════════════════════════════════════════════════
  3 new SitRep(s) processed
  New rows added : 45
  Master total   : 212 rows
══════════════════════════════════════════════════════

Outputs written to: data/processed/epicentre_format/
```

## Output Files

| File | Description |
|------|-------------|
| `data/processed/epicentre_format/<name>/new_cases_counts.csv` | New cases table from this sitrep |
| `data/processed/epicentre_format/<name>/cumulative_counts.csv` | Cumulative cases table |
| `data/processed/epicentre_format/<name>/combined_counts.csv` | Merged new + cumulative |
| `data/processed/epicentre_format/<name>/response_counts.csv` | Patient movement summary (if present) |
| `data/processed/epicentre_format/<name>/poe_counts.csv` | Points d'Entrée summary (if present) |
| `data/raw/<name>/raw_extraction.json` | Raw Claude JSON response (debug) |
| `data/processed/master_combined_counts.csv` | Master linelist — all sitreps combined |
| `data/processed/master_response_counts.csv` | Master patient movement data |
| `data/processed/master_poe_counts.csv` | Master Points d'Entrée data |
| `data/processed/processed.json` | Registry of extracted PDFs (keys are canonical PDF filenames) |

## Error Table

| Error | Cause | Fix |
|-------|-------|-----|
| `ANTHROPIC_API_KEY environment variable not set` | `.env` not sourced | Run `set -a && source .env && set +a` before the script |
| `anthropic.AuthenticationError` | API key invalid or revoked | Verify key at console.anthropic.com; update `.env` |
| `anthropic.RateLimitError` | API quota exceeded | Wait a few minutes and retry; if persistent, check plan limits |
| `anthropic.APITimeoutError` (second attempt) | API unresponsive | Script retries once automatically; if still failing, wait and retry the whole command |
| `ValueError: Failed to extract valid JSON after repair` | Claude returned unparseable output for a PDF | Inspect `data/raw/<name>/raw_extraction.json`; re-run single-PDF extraction: `python3 scripts/extract_sitrep.py data/raw/<name>/<name>.pdf` |
| `WARNING: Rejecting cumulative table — title contains 'alertes'` | Heuristic detected an alerts/investigation table instead of a cases table | Expected for some sitreps; check `combined_counts.csv` manually to confirm new cases were still captured |
| `WARNING: Probable cases > 3× suspected` | Alerts table heuristic triggered | Same as above — verify `combined_counts.csv` is correct |
| `FileNotFoundError: data/raw/<name>/MVE_SitRep_NNN...pdf` | PDF not in archive | Run `sitrep-fetch` first; verify with `ls data/raw/` |
| PDF already in `processed.json`, skipped | Script skips already-extracted PDFs in update mode | Use single-PDF mode to force re-extract |
| Master CSV has duplicate rows | Single-PDF mode run after update mode | Deduplicate: `python3 -c "import pandas as pd; df=pd.read_csv('data/processed/master_combined_counts.csv'); df.drop_duplicates().to_csv('data/processed/master_combined_counts.csv', index=False)"` |

## Notes

- Model: `claude-sonnet-4-6` by default; override with `ANTHROPIC_MODEL` in `.env`
- Two-pass extraction: (1) JSON tables, (2) full-text transcription for context
- `data/processed/processed.json` keys are canonical PDF filenames (e.g., `MVE_SitRep_002_2026-05-18.pdf`)
- The extraction folder for SitRep 002 is `data/processed/epicentre_format/MVE_SitRep_002_2026-05-20/` (date from original download's Last-Modified header) — this is correct and expected
- `ANTHROPIC_API_KEY` must be in environment; it is never read from `.env` automatically — always source `.env` first
