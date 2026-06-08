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
| `0 relevant PDF link(s) found on INSP website` | INSP changed their post-slug naming scheme or PDF embed method | **See "Diagnosing Zero PDFs" below** |
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

---

## Diagnosing Zero PDFs

**When:** the fetcher reports `0 relevant PDF link(s) found` but you believe new SitReps have been posted.

### Step 1 — Check what post slugs are actually on the INSP category page

```python
import re, requests
session = requests.Session()
session.headers["User-Agent"] = "MSF-Epicentre-SitRep-Fetcher/1.0"
html = session.get("https://insp.cd/category/sitrep/", timeout=30).text
for m in re.finditer(r'href=["\']([^"\']*sitrep[^"\']*)["\']', html, re.I):
    print(m.group(1))
```

Look for slugs that don't match the two known patterns:
- **Legacy** (SitReps 001–014): `https://insp.cd/sitrep-mve-n-NNN-2026/`
- **New** (SitReps 015+): `https://insp.cd/sitrep-nNNN-mv[eb]_DD-MM-YYYY/`  or `https://insp.cd/sitrep-nNNN-mve-<description>/`

If new slugs appear that neither regex in `_POST_SLUG_RE` matches, the pattern needs updating.

### Step 2 — Check how a new post embeds its PDF

```python
import re, requests, base64, json
session = requests.Session()
session.headers["User-Agent"] = "MSF-Epicentre-SitRep-Fetcher/1.0"
post_html = session.get("https://insp.cd/<new-slug>/", timeout=30).text

# Check pdfemb iframe (current method as of SitRep 014+)
m = re.search(r'pdfemb-data=([A-Za-z0-9+/=]+)', post_html)
if m:
    data = json.loads(base64.b64decode(m.group(1) + "==").decode())
    print("pdfemb URL:", data.get("url"))
else:
    # Fallback: look for direct <a href=...pdf> links
    for u in re.findall(r'href=["\']([^"\']*\.pdf)["\']', post_html, re.I):
        print("direct:", u)
```

Two embed methods are currently handled by the fetcher:
1. **`<a href=...pdf>`** — direct download link (early SitReps, detected by `a_pattern`)
2. **`pdfemb-data=<base64-JSON>`** in iframe `src` — PDF Embedder plugin (SitRep 014 onwards, detected by `pdfemb-data` pattern in `_scan`)

If a new embed method is found (e.g. a different plugin, a Google Drive embed, a WP file manager), add a new extractor block inside `_scan()` in `scripts/fetch_sitreps.py`.

### Step 3 — Check `_is_relevant` and `_is_recent_enough` filters

If the PDF URL is found but not downloaded, it may be filtered out:
- `_is_relevant()` requires the URL to contain one of `["mve", "mvb", "sitrep", "ebola", "marburg"]` (case-insensitive)
- `_is_recent_enough()` requires a `/wp-content/uploads/YYYY/MM/` path segment ≥ `DEFAULT_SINCE` (`2026/05`)

If INSP upload PDFs to an older date folder or changes naming, update these filters accordingly.
