---
name: sitrep-report
description: "Render and deploy the MVE SitRep Quarto HTML report. Use when: rebuilding the report after new extraction data; Quarto render fails; R package missing or broken; publishing updated report to GitHub Pages via git push; the live report at bquilty25.github.io is out of date; diagnosing R or Quarto errors."
---

# SitRep Report

Renders the Quarto HTML situation report from the master CSVs and deploys it to GitHub Pages.

## When to Use

- After extraction has added new rows to `data/processed/master_combined_counts.csv`
- Diagnosing Quarto or R errors
- Publishing the updated report (always confirm with user before `git push`)

## Render Command

Run from the **project root** (not from inside `scripts/`):

```bash
cd /Users/billyquilty/Documents/Work/bvd_sitrep_extractor
quarto render sitrep_report.qmd
```

Output: `outputs/sitrep_report.html`  
Config: `_quarto.yml` at project root sets `output-dir: outputs`

Expected terminal output ends with:
```
Output created: outputs/sitrep_report.html
```

## R Dependency Check

If render fails with `there is no package called 'X'` or `Error in library(X)`:

```r
Rscript -e "if (!requireNamespace('pacman', quietly=TRUE)) install.packages('pacman'); pacman::p_load(tidyverse, lubridate, here, scales, ggrepel, flextable, ggh4x)"
```

Required packages: `tidyverse`, `lubridate`, `here`, `scales`, `ggrepel`, `flextable`, `ggh4x`

## Deploy Workflow

**Always confirm with the user before running `git push`.**

```bash
# 1. Review what changed
git status
git diff --stat HEAD outputs/sitrep_report.html data/processed/master_combined_counts.csv

# 2. Commit
git add -A
git commit -m "Add SitRep NNN (YYYY-MM-DD), update report"

# 3. Push — triggers GitHub Actions deploy (CONFIRM WITH USER FIRST)
git push
```

After push, the GitHub Actions workflow (`.github/workflows/deploy-report-pages.yml`) fires automatically when `outputs/sitrep_report.html` has changed. Live URL: https://bquilty25.github.io/bvd_sitrep_extractor/sitrep_report.html

## Error Table

| Error | Cause | Fix |
|-------|-------|-----|
| `there is no package called 'X'` | R package not installed | Run the dependency check command above |
| `Error in library(X) : there is no package called 'X'` | Same | Same |
| `quarto: command not found` | Quarto CLI not installed or not in PATH | `brew install quarto` or download from quarto.org; restart terminal |
| `object 'X' not found` in R | Column renamed or missing in master CSV | Check column names in `data/processed/master_combined_counts.csv`; compare against what `sitrep_report.qmd` expects |
| `Error in dmy(count_end_date)` | Malformed date in master CSV | Inspect `count_end_date` column for non-`DD/MM/YYYY` values; trace to the offending sitrep's `combined_counts.csv` |
| `Quarto render error: ... execution halted` | R code chunk error | Read the full R traceback printed above it; fix the specific data issue |
| `git push` rejected (non-fast-forward) | Remote has new commits | Run `git pull --rebase` then `git push` again |
| `git push` authentication failure | HTTPS/SSH credentials expired | Run `gh auth login` (GitHub CLI) or re-add SSH key |
| GitHub Pages not updating after push | CI workflow not triggered or failed | Check Actions tab: github.com/bquilty25/bvd_sitrep_extractor/actions |
| Report renders but plots are empty | Master CSV has no rows for the expected date range | Verify `data/processed/master_combined_counts.csv` has data with `head data/processed/master_combined_counts.csv` |

## Key Files

| File | Description |
|------|-------------|
| `sitrep_report.qmd` | Quarto source document |
| `_quarto.yml` | Project config — sets `output-dir: outputs` |
| `outputs/sitrep_report.html` | Rendered output (git-tracked, deployed to GitHub Pages) |
| `data/processed/master_combined_counts.csv` | Primary data source for the report |
| `data/processed/master_response_counts.csv` | Patient movement data for the report |
| `data/processed/master_poe_counts.csv` | Points d'Entrée data for the report |
| `.github/workflows/deploy-report-pages.yml` | CI/CD deploy workflow |
