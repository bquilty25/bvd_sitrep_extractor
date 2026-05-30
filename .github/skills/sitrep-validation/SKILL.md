---
name: sitrep-validation
description: "Update and validate against the Kraemer lab Ebola DRC 2026 reference dataset. Use when: updating the Kraemer submodule before rendering; the cross-source validation section is missing or stale; git submodule errors; understanding which Kraemer files feed the report; interpreting discrepancies between automated extraction and the Kraemer manually-coded data."
---

# INRB-UMIE Validation Data

The `Ebola_DRC_2026` git submodule (kraemer-lab/Ebola_DRC_2026) provides a manually-coded reference dataset that the report compares against automated extraction outputs in the "Cross-source validation" section.

## When to Use

- Before rendering — to pull the latest INRB-UMIE data so the validation section reflects current numbers
- Diagnosing why the cross-source validation plots are stale or empty
- Understanding a discrepancy flagged in the rendered report

## Update Command

```bash
cd /Users/billyquilty/Documents/Work/bvd_sitrep_extractor
git submodule update --remote Ebola_DRC_2026
```

This pulls the latest commit from `https://github.com/kraemer-lab/Ebola_DRC_2026` into the pinned `Ebola_DRC_2026/` directory.

After updating, confirm new data arrived:

```bash
git submodule status Ebola_DRC_2026
# Shows: <new_commit_hash> Ebola_DRC_2026 (build-YYYY-MM-DD-...)
git -C Ebola_DRC_2026 log --oneline -3
```

## Files Used by the Report

All under `Ebola_DRC_2026/build/long/`:

| File | Used for |
|------|----------|
| `insp_sitrep__cumulative_confirmed_cases.csv` | Confirmed case counts by zone |
| `insp_sitrep__cumulative_suspected_cases.csv` | Suspected case counts by zone |
| `insp_sitrep__cumulative_suspected_deaths.csv` | Suspected deaths by zone |
| `insp_sitrep__cumulative_confirmed_deaths.csv` | Confirmed deaths by zone |
| `insp_sitrep__hospitalised.csv` | Hospitalised patients |
| `insp_sitrep__in_bed_previous_day.csv` | Response indicators |
| `insp_sitrep__new_hosp_admissions.csv` | New admissions |
| `insp_sitrep__new_hosp_detainees.csv` | New detainees |
| `insp_sitrep__new_hosp_other.csv` | Other new admissions |
| `insp_sitrep__cumulative_contacts_traced.csv` | Contact tracing totals |

Zone name differences are harmonised in the report via `KRAEMER_NAME_MAP`:
- `"Mongbalu"` → `"Mongbwalu"`
- `"Nyakunde"` → `"Nyankunde"`

## Interpreting the Cross-Source Comparison

The validation section overlays this extraction's values against Kraemer lab values on the same chart. Expected behaviour:

| Observation | Meaning |
|-------------|---------|
| Lines overlap closely | Extraction and INRB-UMIE agree — good |
| INRB-UMIE lags by 1–2 sitreps | INRB-UMIE dataset not yet updated; re-run submodule update later |
| Systematic offset for one zone | Possible zone name mismatch or aggregation difference; check `KRAEMER_NAME_MAP` |
| Extraction higher than INRB-UMIE | Extraction may be capturing aggregate rows that INRB-UMIE excludes; check `is_aggregate` column in master CSV |
| Large isolated spike | Likely a table parsing error in one sitrep; inspect `data/raw/<name>/raw_extraction.json` for the outlier date |

## Committing the Submodule Pin

After updating, the new commit hash should be committed with the rest of the pipeline output:

```bash
git add Ebola_DRC_2026
git commit -m "Update INRB-UMIE submodule to YYYY-MM-DD build"
# This can be combined with the report commit:
# git commit -m "Add SitRep NNN, update INRB-UMIE submodule, rebuild report"
```

## Error Table

| Error | Cause | Fix |
|-------|-------|-----|
| `fatal: no submodule mapping found in .gitmodules` | Submodule not initialised | Run `git submodule init && git submodule update` |
| `fatal: repository 'https://github.com/kraemer-lab/Ebola_DRC_2026' not found` | Network or GitHub auth issue | Check internet; re-authenticate with `gh auth login` if using HTTPS |
| `Error in read_csv(...) : cannot open the connection` | CSV file missing from submodule | Submodule may be partially initialised; run `git submodule update --init --recursive` |
| Cross-source section renders blank | Kraemer files exist but all rows filtered out | Check that `zone` values in Kraemer data match `ZONE_LEVELS` defined in `sitrep_report.qmd` |
| Submodule is already up to date | INRB-UMIE has not pushed a new build | Check https://github.com/kraemer-lab/Ebola_DRC_2026/commits for the latest build date |
