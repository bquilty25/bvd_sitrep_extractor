---
name: sitrep-fetch
description: "Fetch new MVE SitRep PDFs from the INSP DRC website. Use when: downloading new PDFs from INSP; checking whether INSP has posted a new sitrep; the fetch script fails or returns unexpected output; understanding why a PDF was skipped as a duplicate; manifest corruption or rebuild."
---

# SitRep Fetch

Downloads new MVE situation report PDFs from the INSP DRC website and maintains the PDF archive manifest.

## When to Use

- Before running extraction — to check whether INSP has posted new data
- Diagnosing fetch failures (network, SSL, 403, zero PDFs found)
- Understanding duplicate-skipping behaviour
- Rebuilding or inspecting `data/raw/manifest.json`

## Command

```bash
cd /Users/billyquilty/Documents/Work/bvd_sitrep_extractor
python3 scripts/fetch_sitreps.py
```

Optional flags:

| Flag | Default | Purpose |
|------|---------|---------|
| `--pdf-dir DIR` | `data/raw/` | Local PDF archive directory |
| `--since YYYY-MM` | `2026-05` | Ignore PDFs older than this month |
| `--pages URL ...` | INSP ebola + category/sitrep pages | Pages to scrape |

## Interpreting Output

| Output | Meaning | Next step |
|--------|---------|-----------|
| `N new PDF(s) downloaded → data/raw/` | New sitreps available | Proceed to `sitrep-extract` |
| `No new PDFs — archive is up to date (N PDF(s) total)` | INSP has not posted a new sitrep | No action needed; check again tomorrow |
| `Duplicate of MVE_SitRep_XXX_YYYY-MM-DD.pdf (same MD5) — skipping` | INSP reused an existing PDF at a new URL | Expected; manifest updated with `duplicate_of` field; not an error |

## Error Table

| Error | Cause | Fix |
|-------|-------|-----|
| `Warning: could not fetch https://insp.cd/...: ConnectionError` | Network or DNS failure | Retry in a few minutes; check internet connectivity |
| `Warning: could not fetch ...: SSLError` | Certificate validation failure | Retry; if persistent, check system trust store |
| `Warning: could not fetch ...: 403 Client Error` | Rate-limited or geo-blocked | Wait 5–10 minutes then retry |
| `0 relevant PDF link(s) found on INSP website` | INSP site structure changed | Visit the INSP Ebola page manually; if structure changed, update `DEFAULT_PAGES` or `_is_sitrep_link()` in `scripts/fetch_sitreps.py` |
| `json.JSONDecodeError` reading manifest | `data/raw/manifest.json` corrupted | Delete it with `rm data/raw/manifest.json` and re-run fetch — the script rebuilds the manifest from the existing PDFs in `data/raw/` |
| No output / script hangs | Network timeout | Interrupt with Ctrl-C and retry |

## Key Files

| File | Description |
|------|-------------|
| `data/raw/manifest.json` | Registry of all known URLs → canonical PDF names; includes `duplicate_of` for deduplicated entries |
| `data/raw/<sitrep_name>/MVE_SitRep_NNN_YYYY-MM-DD.pdf` | Canonical PDF archive (one per sitrep subdirectory) |
| `data/processed/processed.json` | Extraction registry (updated by `sitrep-extract`, not this step) |

## Notes

- `data/raw/` is in `.gitignore` — `data/raw/manifest.json` is **not** git-tracked
- PDF filenames follow `MVE_SitRep_NNN_YYYY-MM-DD.pdf` where the date comes from the server `Last-Modified` header
- **No SitRep 003 exists** — INSP's "sitrep-mve-n-003" page serves the same PDF as SitRep 004 (identical MD5); the fetch script records it as a duplicate and skips it
- Deduplication is content-hash based (MD5) — a PDF at a new URL is skipped if its content matches any already-archived PDF
