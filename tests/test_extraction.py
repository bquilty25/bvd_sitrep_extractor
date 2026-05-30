"""
Test suite for SitRep BVD DRC extraction pipeline.

Tests are grouped into three categories:
  1. Unit tests  — pure logic from extract_sitrep.py (no API, no files needed)
  2. Schema tests — validate the structure of raw_extraction.json
  3. Data tests   — validate the content of the CSV/Excel outputs

Run after extract_sitrep.py has been executed at least once:
  pytest tests/ -v
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

# Allow imports from scripts/
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from extract_sitrep import (
    clean_json_text, build_dataframe, coerce_numerics,
    parse_french_date, extract_date_from_filename, build_combined_counts, _nd,
    COMBINED_COLS, normalise_zone, _row_has_data,
    _sitrep_series_key, _sitrep_revision, _dedupe_latest_revision, _is_aggregate,
)

# ── Paths ──────────────────────────────────────────────────────────────────────

DATA_PROCESSED = Path(__file__).parent.parent / "data" / "processed"
DATA_RAW       = Path(__file__).parent.parent / "data" / "raw"


def _find_latest_output_dir(epicentre_dir: Path) -> Path | None:
    """Return the most recently modified sitrep subdirectory under epicentre_format/, or None."""
    if not epicentre_dir.exists():
        return None
    candidates = [
        d for d in epicentre_dir.iterdir()
        if d.is_dir() and (d / "combined_counts.csv").exists()
    ]
    return (
        max(candidates, key=lambda d: (d / "combined_counts.csv").stat().st_mtime)
        if candidates else None
    )


_SAMPLE_DIR   = _find_latest_output_dir(DATA_PROCESSED / "epicentre_format")
RAW_JSON      = (DATA_RAW / _SAMPLE_DIR.name / "raw_extraction.json") if _SAMPLE_DIR else (DATA_RAW / "raw_extraction.json")
NEW_CASES_CSV = (_SAMPLE_DIR / "new_cases_counts.csv")  if _SAMPLE_DIR else (DATA_PROCESSED / "epicentre_format" / "new_cases_counts.csv")
CUMUL_CSV     = (_SAMPLE_DIR / "cumulative_counts.csv") if _SAMPLE_DIR else (DATA_PROCESSED / "epicentre_format" / "cumulative_counts.csv")
COMBINED_CSV  = (_SAMPLE_DIR / "combined_counts.csv")   if _SAMPLE_DIR else (DATA_PROCESSED / "epicentre_format" / "combined_counts.csv")


# ─────────────────────────────────────────────────────────────────────────────
# 1. UNIT TESTS  (no files / API required)
# ─────────────────────────────────────────────────────────────────────────────

class TestCleanJsonText:
    def test_strips_plain_whitespace(self):
        assert clean_json_text("  {}  ") == "{}"

    def test_strips_json_fences(self):
        raw = "```json\n{}\n```"
        assert clean_json_text(raw) == "{}"

    def test_strips_generic_fences(self):
        raw = "```\n{}\n```"
        assert clean_json_text(raw) == "{}"

    def test_preserves_content(self):
        raw = '```json\n{"key": "value"}\n```'
        result = clean_json_text(raw)
        parsed = json.loads(result)
        assert parsed == {"key": "value"}

    def test_no_fence_passes_through(self):
        payload = '{"a": 1}'
        assert clean_json_text(payload) == payload


class TestBuildDataframe:
    def test_empty_rows_returns_empty_df_with_columns(self):
        table = {"columns": ["A", "B"], "rows": []}
        df = build_dataframe(table)
        assert list(df.columns) == ["A", "B"]
        assert len(df) == 0

    def test_basic_rows(self):
        table = {
            "columns": ["Zone", "Cas confirmés"],
            "rows": [
                {"Zone": "Bikoro", "Cas confirmés": "3"},
                {"Zone": "Mbandaka", "Cas confirmés": "7"},
            ],
        }
        df = build_dataframe(table)
        assert len(df) == 2
        assert "Zone" in df.columns

    def test_numeric_coercion_applied(self):
        table = {
            "columns": ["Zone", "Cas"],
            "rows": [{"Zone": "A", "Cas": "5"}, {"Zone": "B", "Cas": "12"}],
        }
        df = build_dataframe(table)
        assert pd.api.types.is_numeric_dtype(df["Cas"])

    def test_dash_cells_become_nan(self):
        table = {
            "columns": ["Zone", "Décès"],
            "rows": [{"Zone": "A", "Décès": "-"}, {"Zone": "B", "Décès": "3"}],
        }
        df = build_dataframe(table)
        # After coercion the dash should be NaN (or 0), not a string
        assert df["Décès"].iloc[1] == 3.0


class TestCoerceNumerics:
    def test_mixed_column_stays_object_if_mostly_text(self):
        df = pd.DataFrame({"col": ["hello", "world", "foo", "bar", "1"]})
        result = coerce_numerics(df.copy())
        # Only 1/5 numeric — should NOT be cast
        assert not pd.api.types.is_numeric_dtype(result["col"])

    def test_pure_numeric_column_is_cast(self):
        df = pd.DataFrame({"col": ["1", "2", "3", "4", "5"]})
        result = coerce_numerics(df.copy())
        assert pd.api.types.is_numeric_dtype(result["col"])

    def test_em_dash_treated_as_missing(self):
        df = pd.DataFrame({"col": ["—", "2", "3"]})
        result = coerce_numerics(df.copy())
        assert pd.isna(result["col"].iloc[0])


class TestParseFrenchDate:
    def test_standard_date(self):
        assert parse_french_date("au 19 mai 2026") == "19/05/2026"

    def test_date_in_long_title(self):
        title = "Tableau III. Répartition … au 19 mai 2026"
        assert parse_french_date(title) == "19/05/2026"

    def test_single_digit_day(self):
        assert parse_french_date("au 3 mars 2026") == "03/03/2026"

    def test_other_months(self):
        assert parse_french_date("1 janvier 2025") == "01/01/2025"
        assert parse_french_date("31 décembre 2024") == "31/12/2024"

    def test_no_date_returns_empty(self):
        assert parse_french_date("aucune date ici") == ""


class TestExtractDateFromFilename:
    def test_standard_filename(self):
        from pathlib import Path
        p = Path("Draft_SitRep_MVE_RDC_20260520_vf1.pdf")
        assert extract_date_from_filename(p) == "20/05/2026"

    def test_no_date_returns_empty(self):
        assert extract_date_from_filename(Path("no_date.pdf")) == ""


class TestSitrepRevisionHandling:
    def test_series_key_strips_revision_suffix(self):
        assert _sitrep_series_key("MVE_SitRep_012_2026-05-27_v2") == "MVE_SitRep_012_2026-05-27"

    def test_revision_defaults_to_one(self):
        assert _sitrep_revision("MVE_SitRep_012_2026-05-27") == 1

    def test_revision_suffix_is_parsed(self):
        assert _sitrep_revision("MVE_SitRep_012_2026-05-27_v2") == 2

    def test_dedupe_latest_revision_keeps_newer_source(self):
        df = pd.DataFrame([
            {
                "count_type": "Cumules",
                "zone": "Total",
                "count_end_date": "26/05/2026",
                "sitrep_source": "MVE_SitRep_012_2026-05-27",
                "cases_confirmed": "110",
            },
            {
                "count_type": "Cumules",
                "zone": "Total",
                "count_end_date": "26/05/2026",
                "sitrep_source": "MVE_SitRep_012_2026-05-27_v2",
                "cases_confirmed": "121",
            },
        ])

        result = _dedupe_latest_revision(
            df,
            subset=["count_type", "zone", "count_end_date"],
        )

        assert len(result) == 1
        assert result.iloc[0]["sitrep_source"] == "MVE_SitRep_012_2026-05-27_v2"
        assert result.iloc[0]["cases_confirmed"] == "121"

    def test_dedupe_latest_revision_preserves_distinct_province_aggregates(self):
        df = pd.DataFrame([
            {
                "count_type": "Nouveaux",
                "count_start_date": "21/05/2026",
                "count_end_date": "21/05/2026",
                "zone": "",
                "province": "Ituri",
                "sitrep_source": "MVE_SitRep_006_2026-05-21",
                "cases_confirmed": "12",
            },
            {
                "count_type": "Nouveaux",
                "count_start_date": "21/05/2026",
                "count_end_date": "21/05/2026",
                "zone": "",
                "province": "",
                "sitrep_source": "MVE_SitRep_006_2026-05-21",
                "cases_confirmed": "13",
            },
        ])

        result = _dedupe_latest_revision(
            df,
            subset=["count_type", "count_start_date", "count_end_date", "zone", "province"],
        )

        assert len(result) == 2
        assert set(result["cases_confirmed"]) == {"12", "13"}


class TestAggregateDetection:
    def test_blank_zone_and_province_is_aggregate(self):
        assert _is_aggregate("", "") == "TRUE"


class TestNdHelper:
    def test_nd_returns_empty(self):
        assert _nd("ND") == ""
        assert _nd("nd") == ""

    def test_n_returns_empty(self):
        assert _nd("N") == ""

    def test_dash_returns_empty(self):
        assert _nd("-") == ""

    def test_zero_preserved(self):
        assert _nd("0") == "0"

    def test_number_preserved(self):
        assert _nd("42") == "42"


class TestBuildCombinedCounts:
    @pytest.fixture
    def sample_raw(self):
        _COLS = ["province", "zone_de_sante", "cas_suspects", "cas_probables",
                 "cas_confirmes", "deces_suspects", "deces_probables", "deces_confirmes"]
        return {
            "new_cases": {
                "table_title": "",
                "columns": _COLS,
                "rows": [
                    {"province": "Ituri", "zone_de_sante": "Bunia",
                     "cas_confirmes": "3", "deces_confirmes": "1",
                     "cas_probables": "ND", "cas_suspects": "10",
                     "deces_suspects": "", "deces_probables": ""},
                    {"province": "Total", "zone_de_sante": "",
                     "cas_confirmes": "3", "deces_confirmes": "1",
                     "cas_probables": "0", "cas_suspects": "10",
                     "deces_suspects": "", "deces_probables": ""},
                ],
                "notes": "",
            },
            "cumulative": {
                "table_title": "Tableau III. … au 19 mai 2026",
                "columns": _COLS,
                "rows": [
                    {"province": "Ituri", "zone_de_sante": "Bunia",
                     "cas_suspects": "90", "deces_suspects": "23",
                     "cas_confirmes": "6", "cas_probables": "",
                     "deces_probables": "", "deces_confirmes": ""},
                ],
                "notes": "",
            },
        }

    def test_has_correct_columns(self, sample_raw):
        df = build_combined_counts(sample_raw, "20/05/2026")
        assert list(df.columns) == COMBINED_COLS

    def test_row_count(self, sample_raw):
        df = build_combined_counts(sample_raw, "20/05/2026")
        # 2 new-cases rows + 1 cumulative row
        assert len(df) == 3

    def test_count_types_present(self, sample_raw):
        df = build_combined_counts(sample_raw, "20/05/2026")
        assert set(df["count_type"].unique()) == {"Nouveaux", "Cumules"}

    def test_nouveaux_dates(self, sample_raw):
        df = build_combined_counts(sample_raw, "20/05/2026")
        nc = df[df["count_type"] == "Nouveaux"]
        assert (nc["count_start_date"] == "20/05/2026").all()
        assert (nc["count_end_date"]   == "20/05/2026").all()

    def test_cumules_end_date_from_title(self, sample_raw):
        df = build_combined_counts(sample_raw, "20/05/2026")
        cum = df[df["count_type"] == "Cumules"]
        assert (cum["count_end_date"] == "19/05/2026").all()

    def test_cumules_start_date_empty(self, sample_raw):
        df = build_combined_counts(sample_raw, "20/05/2026")
        cum = df[df["count_type"] == "Cumules"]
        assert (cum["count_start_date"] == "").all()

    def test_nd_stripped(self, sample_raw):
        df = build_combined_counts(sample_raw, "20/05/2026")
        nc = df[df["count_type"] == "Nouveaux"]
        # "Nouveaux cas probables" was "ND" for Bunia row
        assert nc.iloc[0]["cases_probable"] == ""

    def test_zero_preserved(self, sample_raw):
        df = build_combined_counts(sample_raw, "20/05/2026")
        nc = df[df["count_type"] == "Nouveaux"]
        # Total row has "0" for probables — should stay as "0"
        assert nc.iloc[1]["cases_probable"] == "0"

    def test_nouveaux_deaths_suspected_empty(self, sample_raw):
        """New-cases table has no deaths_suspected column — must be blank."""
        df = build_combined_counts(sample_raw, "20/05/2026")
        nc = df[df["count_type"] == "Nouveaux"]
        assert (nc["deaths_suspected"] == "").all()

    def test_cumules_cases_probable_empty(self, sample_raw):
        """Cumulative table has no cases_probable — must be blank."""
        df = build_combined_counts(sample_raw, "20/05/2026")
        cum = df[df["count_type"] == "Cumules"]
        assert (cum["cases_probable"] == "").all()

    def test_sitrep_source_column_present(self, sample_raw):
        df = build_combined_counts(sample_raw, "20/05/2026", source="test_sitrep")
        assert "sitrep_source" in df.columns
        assert (df["sitrep_source"] == "test_sitrep").all()

    def test_empty_rows_filtered(self):
        """Rows with all-empty numeric fields must be dropped."""
        _COLS = ["province", "zone_de_sante", "cas_suspects", "cas_probables",
                 "cas_confirmes", "deces_suspects", "deces_probables", "deces_confirmes"]
        raw = {
            "new_cases": {
                "table_title": "",
                "columns": _COLS,
                "rows": [
                    {"province": "Ituri", "zone_de_sante": "Bunia",
                     "cas_suspects": "10", "cas_probables": "", "cas_confirmes": "3",
                     "deces_suspects": "", "deces_probables": "", "deces_confirmes": ""},
                    # all-empty row — should be dropped
                    {"province": "Ituri", "zone_de_sante": "Rwampara",
                     "cas_suspects": "", "cas_probables": "", "cas_confirmes": "",
                     "deces_suspects": "", "deces_probables": "", "deces_confirmes": ""},
                ],
                "notes": "",
            },
            "cumulative": {"table_title": "", "columns": _COLS, "rows": [], "notes": ""},
        }
        df = build_combined_counts(raw, "20/05/2026")
        assert len(df) == 1
        assert df.iloc[0]["zone"] == "Bunia"


class TestNormaliseZone:
    def test_mungbwalu_normalised(self):
        assert normalise_zone("Mungbwalu") == "Mongbwalu"

    def test_bambu_case_insensitive(self):
        assert normalise_zone("BAMBU") == "Bambu"

    def test_unknown_zone_unchanged(self):
        assert normalise_zone("Bunia") == "Bunia"

    def test_strips_whitespace(self):
        assert normalise_zone("  Bunia  ") == "Bunia"


class TestRowHasData:
    def test_returns_true_with_one_populated_field(self):
        r = {"cas_suspects": "5", "cas_probables": "", "cas_confirmes": "",
             "deces_suspects": "", "deces_probables": "", "deces_confirmes": ""}
        assert _row_has_data(r) is True

    def test_returns_false_when_all_empty(self):
        r = {"cas_suspects": "", "cas_probables": "", "cas_confirmes": "",
             "deces_suspects": "", "deces_probables": "", "deces_confirmes": ""}
        assert _row_has_data(r) is False

    def test_nd_sentinel_counts_as_empty(self):
        r = {"cas_suspects": "ND", "cas_probables": "", "cas_confirmes": "",
             "deces_suspects": "", "deces_probables": "", "deces_confirmes": ""}
        assert _row_has_data(r) is False

    def test_zero_counts_as_data(self):
        r = {"cas_suspects": "0", "cas_probables": "", "cas_confirmes": "",
             "deces_suspects": "", "deces_probables": "", "deces_confirmes": ""}
        assert _row_has_data(r) is True


# ─────────────────────────────────────────────────────────────────────────────
# 2. SCHEMA TESTS  (require raw_extraction.json)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def raw():
    if not RAW_JSON.exists():
        pytest.skip("raw_extraction.json not found — run extract_sitrep.py first")
    with open(RAW_JSON, encoding="utf-8") as fh:
        return json.load(fh)


class TestRawJsonSchema:
    def test_top_level_keys(self, raw):
        assert "new_cases" in raw, "Missing key: new_cases"
        assert "cumulative" in raw, "Missing key: cumulative"

    @pytest.mark.parametrize("table_key", ["new_cases", "cumulative"])
    def test_each_table_has_required_fields(self, raw, table_key):
        t = raw[table_key]
        assert "columns" in t,     f"{table_key}: missing 'columns'"
        assert "rows"    in t,     f"{table_key}: missing 'rows'"
        assert "notes"   in t,     f"{table_key}: missing 'notes'"
        assert isinstance(t["columns"], list), f"{table_key}: 'columns' must be a list"
        assert isinstance(t["rows"],    list), f"{table_key}: 'rows' must be a list"

    @pytest.mark.parametrize("table_key", ["new_cases", "cumulative"])
    def test_columns_not_empty(self, raw, table_key):
        assert len(raw[table_key]["columns"]) > 0, f"{table_key}: columns list is empty"

    @pytest.mark.parametrize("table_key", ["new_cases", "cumulative"])
    def test_rows_not_empty(self, raw, table_key):
        assert len(raw[table_key]["rows"]) > 0, f"{table_key}: rows list is empty"

    @pytest.mark.parametrize("table_key", ["new_cases", "cumulative"])
    def test_row_keys_match_columns(self, raw, table_key):
        t       = raw[table_key]
        columns = set(t["columns"])
        for i, row in enumerate(t["rows"]):
            row_keys = set(row.keys())
            assert row_keys == columns, (
                f"{table_key} row {i}: keys {row_keys} != columns {columns}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# 3. DATA / OUTPUT TESTS  (require CSV / Excel outputs)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def new_cases():
    if not NEW_CASES_CSV.exists():
        pytest.skip("new_cases_counts.csv not found — run extract_sitrep.py first")
    return pd.read_csv(NEW_CASES_CSV)


@pytest.fixture(scope="session")
def cumulative():
    if not CUMUL_CSV.exists():
        pytest.skip("cumulative_counts.csv not found — run extract_sitrep.py first")
    return pd.read_csv(CUMUL_CSV)


@pytest.fixture(scope="session")
def combined():
    if not COMBINED_CSV.exists():
        pytest.skip("combined_counts.csv not found — run extract_sitrep.py first")
    return pd.read_csv(COMBINED_CSV, dtype=str, keep_default_na=False)


# ─────────────────────────────────────────────────────────────────────────────
# 3. DATA / OUTPUT TESTS  (require CSV / Excel outputs)
# ─────────────────────────────────────────────────────────────────────────────

class TestOutputFiles:
    def test_new_cases_csv_exists(self):
        if not NEW_CASES_CSV.exists():
            pytest.skip("new_cases_counts.csv not found — run extract_sitrep.py first")

    def test_cumulative_csv_exists(self):
        if not CUMUL_CSV.exists():
            pytest.skip("cumulative_counts.csv not found — run extract_sitrep.py first")

    def test_combined_csv_exists(self):
        if not COMBINED_CSV.exists():
            pytest.skip("combined_counts.csv not found — run extract_sitrep.py first")

    def test_new_cases_csv_readable(self, new_cases):
        assert new_cases is not None

    def test_cumulative_csv_readable(self, cumulative):
        assert cumulative is not None

    def test_combined_csv_readable(self, combined):
        assert combined is not None

    def test_csvs_are_valid_utf8(self):
        for path in (NEW_CASES_CSV, CUMUL_CSV, COMBINED_CSV):
            if not path.exists():
                pytest.skip(f"{path.name} not found — run extract_sitrep.py first")
            path.read_bytes().decode("utf-8-sig")  # raises if invalid


class TestNewCasesContent:
    def test_has_rows(self, new_cases):
        assert len(new_cases) > 0, "New cases table has no rows"

    def test_has_columns(self, new_cases):
        assert len(new_cases.columns) > 0

    def test_has_location_or_case_column(self, new_cases):
        joined = " ".join(c.lower() for c in new_cases.columns)
        keywords = ["zone", "province", "aire", "district", "localit",
                    "cas", "case", "confirm", "probable", "suspect"]
        assert any(kw in joined for kw in keywords), (
            f"No recognisable column found. Columns: {list(new_cases.columns)}"
        )

    def test_no_fully_duplicate_data_rows(self, new_cases):
        """Total/subtotal rows are excluded from the duplicate check."""
        first_col = new_cases.columns[0]
        non_total = new_cases[
            ~new_cases[first_col]
            .astype(str)
            .str.lower()
            .str.contains(r"total|sous.total|cumul", regex=True, na=False)
        ]
        dupes = non_total.duplicated().sum()
        assert dupes == 0, f"{dupes} duplicate data rows in new_cases"

    def test_numeric_columns_non_negative(self, new_cases):
        for col in new_cases.select_dtypes(include="number").columns:
            vals = new_cases[col].dropna()
            neg  = vals[vals < 0]
            assert len(neg) == 0, f"Negative values in '{col}': {neg.tolist()}"

    def test_no_completely_empty_rows(self, new_cases):
        all_null = new_cases.isnull().all(axis=1).sum()
        assert all_null == 0, f"{all_null} completely empty rows in new_cases"


class TestCumulativeContent:
    def test_has_rows(self, cumulative):
        assert len(cumulative) > 0, "Cumulative table has no rows"

    def test_has_columns(self, cumulative):
        assert len(cumulative.columns) > 0

    def test_has_location_or_case_column(self, cumulative):
        joined = " ".join(c.lower() for c in cumulative.columns)
        keywords = ["zone", "province", "aire", "district", "localit",
                    "cas", "case", "confirm", "probable", "suspect", "cumul"]
        assert any(kw in joined for kw in keywords), (
            f"No recognisable column found. Columns: {list(cumulative.columns)}"
        )

    def test_no_fully_duplicate_data_rows(self, cumulative):
        first_col = cumulative.columns[0]
        non_total = cumulative[
            ~cumulative[first_col]
            .astype(str)
            .str.lower()
            .str.contains(r"total|sous.total|cumul", regex=True, na=False)
        ]
        dupes = non_total.duplicated().sum()
        assert dupes == 0, f"{dupes} duplicate data rows in cumulative"

    def test_numeric_columns_non_negative(self, cumulative):
        for col in cumulative.select_dtypes(include="number").columns:
            vals = cumulative[col].dropna()
            neg  = vals[vals < 0]
            assert len(neg) == 0, f"Negative values in '{col}': {neg.tolist()}"

    def test_no_completely_empty_rows(self, cumulative):
        all_null = cumulative.isnull().all(axis=1).sum()
        assert all_null == 0, f"{all_null} completely empty rows in cumulative"

    def test_cumulative_counts_gte_new_cases(self, new_cases, cumulative):
        """
        For any numeric column that appears in both tables, the cumulative
        total should be >= the new-cases total (since cumulative = sum over time).
        Columns are matched by lowercased name.
        """
        nc_num  = new_cases.select_dtypes(include="number")
        cum_num = cumulative.select_dtypes(include="number")

        nc_cols  = {c.lower().strip(): c for c in nc_num.columns}
        cum_cols = {c.lower().strip(): c for c in cum_num.columns}

        shared = set(nc_cols) & set(cum_cols)
        if not shared:
            pytest.skip("No matching numeric columns between the two tables")

        for key in shared:
            # Use min_count=1 so that all-NaN columns return NaN instead of 0.0
            # (a column present in both schemas but never populated shouldn't
            # trigger a false failure).
            nc_total  = nc_num[nc_cols[key]].sum(min_count=1)
            cum_total = cum_num[cum_cols[key]].sum(min_count=1)
            if pd.isna(nc_total) or pd.isna(cum_total):
                continue
            assert cum_total >= nc_total, (
                f"Column '{key}': cumulative total ({cum_total}) < "
                f"new-cases total ({nc_total})"
            )


class TestCombinedContent:
    DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")

    def test_has_exact_columns(self, combined):
        assert list(combined.columns) == COMBINED_COLS

    def test_has_rows(self, combined):
        assert len(combined) > 0

    def test_count_type_values(self, combined):
        allowed = {"Nouveaux", "Cumules"}
        bad = set(combined["count_type"].unique()) - allowed
        assert not bad, f"Unexpected count_type values: {bad}"

    def test_both_count_types_present(self, combined):
        assert "Nouveaux" in combined["count_type"].values
        assert "Cumules"  in combined["count_type"].values

    def test_nouveaux_count_matches_new_cases_rows(self, combined, raw):
        expected = len(raw["new_cases"]["rows"])
        actual   = (combined["count_type"] == "Nouveaux").sum()
        assert actual == expected

    def test_cumules_count_matches_cumulative_rows(self, combined, raw):
        expected = len(raw["cumulative"]["rows"])
        actual   = (combined["count_type"] == "Cumules").sum()
        assert actual == expected

    def test_nouveaux_dates_valid_format(self, combined):
        nc = combined[combined["count_type"] == "Nouveaux"]
        for col in ("count_start_date", "count_end_date"):
            bad = nc[~nc[col].str.match(r"^\d{2}/\d{2}/\d{4}$")]
            assert len(bad) == 0, f"Bad date format in {col}: {bad[col].tolist()}"

    def test_cumules_end_date_valid_format(self, combined):
        cum = combined[combined["count_type"] == "Cumules"]
        bad = cum[~cum["count_end_date"].str.match(r"^\d{2}/\d{2}/\d{4}$")]
        assert len(bad) == 0, f"Bad date format in count_end_date: {bad['count_end_date'].tolist()}"

    def test_cumules_start_date_empty(self, combined):
        cum = combined[combined["count_type"] == "Cumules"]
        assert (cum["count_start_date"] == "").all(), \
            "Cumules rows should have no count_start_date"

    def test_no_nd_sentinel_values_remain(self, combined):
        sentinels = {"ND", "N/A", "N"}
        for col in COMBINED_COLS:
            bad = combined[combined[col].isin(sentinels)]
            assert len(bad) == 0, \
                f"Sentinel value found in column '{col}': {bad[col].tolist()}"

    def test_province_zone_not_all_empty(self, combined):
        assert combined["province"].str.strip().ne("").any(), \
            "All province values are empty"
        assert combined["zone"].str.strip().ne("").any(), \
            "All zone values are empty"


# ─────────────────────────────────────────────────────────────────────────────
# 4. PIPELINE STATUS TESTS  (unprocessed PDFs)
# ─────────────────────────────────────────────────────────────────────────────

PDF_DIR        = Path(__file__).parent.parent / "data" / "raw"
SITREPS_DIR    = DATA_PROCESSED / "epicentre_format"
PROCESSED_JSON = DATA_PROCESSED / "processed.json"
MASTER_CSV     = DATA_PROCESSED / "master_combined_counts.csv"
KRAEMER_LONG   = Path(__file__).parent.parent / "Ebola_DRC_2026" / "build" / "long"


class TestPipelineStatus:
    """Catch PDFs that have been downloaded but not yet extracted."""

    def test_no_unprocessed_pdfs(self):
        if not PDF_DIR.exists():
            pytest.skip("data/raw/ directory not found")
        if not PROCESSED_JSON.exists():
            pytest.skip("data/processed/processed.json not found — run extract_sitrep.py first")

        with open(PROCESSED_JSON, encoding="utf-8") as fh:
            processed = json.load(fh)

        canonical_pdfs = sorted(p.name for p in PDF_DIR.glob("**/MVE_SitRep_*.pdf"))
        if not canonical_pdfs:
            pytest.skip("No canonical PDFs found in data/raw/")

        unprocessed = [name for name in canonical_pdfs if name not in processed]
        assert not unprocessed, (
            f"{len(unprocessed)} downloaded PDF(s) not yet extracted — "
            f"run: python3 scripts/extract_sitrep.py --update\n"
            + "\n".join(f"  {name}" for name in unprocessed)
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5. EXTRACTION SCHEMA TESTS  (format drift detection across all sitreps)
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractionSchema:
    """Catch format changes in new sitreps that would break downstream consumers."""

    def test_master_combined_has_exact_columns(self):
        if not MASTER_CSV.exists():
            pytest.skip("master_combined_counts.csv not found")
        df = pd.read_csv(MASTER_CSV, dtype=str, keep_default_na=False)
        assert list(df.columns) == COMBINED_COLS, (
            f"Schema drift in master_combined_counts.csv\n"
            f"  Expected : {COMBINED_COLS}\n"
            f"  Got      : {list(df.columns)}"
        )

    def test_all_sitrep_combined_csvs_have_correct_schema(self):
        if not SITREPS_DIR.exists():
            pytest.skip("data/processed/epicentre_format/ not found")
        bad = []
        for d in sorted(SITREPS_DIR.iterdir()):
            csv = d / "combined_counts.csv"
            if not d.is_dir() or not csv.exists():
                continue
            df = pd.read_csv(csv, dtype=str, keep_default_na=False)
            if list(df.columns) != COMBINED_COLS:
                bad.append(f"{d.name}: {list(df.columns)}")
        assert not bad, (
            f"Schema drift in {len(bad)} sitrep output(s):\n"
            + "\n".join(f"  {b}" for b in bad)
        )

    def test_zone_values_not_garbled(self):
        if not MASTER_CSV.exists():
            pytest.skip("master_combined_counts.csv not found")
        df = pd.read_csv(MASTER_CSV, dtype=str, keep_default_na=False)
        zones = df["zone"][df["zone"].str.strip() != ""]
        # JSON artifacts, numeric-only values, or escape sequences suggest
        # extraction failure (Claude returned table structure instead of data).
        bad = zones[
            zones.str.match(r'^[\{\[\d"\'\\]') |
            zones.str.contains(r"\\[ntr]", regex=True) |
            zones.str.len().gt(60)
        ]
        assert len(bad) == 0, (
            f"Garbled zone values — likely extraction failure:\n"
            f"{bad.value_counts().to_string()}"
        )

    def test_dates_in_expected_range(self):
        if not MASTER_CSV.exists():
            pytest.skip("master_combined_counts.csv not found")
        df = pd.read_csv(MASTER_CSV, dtype=str, keep_default_na=False)
        dates = df.loc[df["count_end_date"] != "", "count_end_date"]

        bad_format = dates[~dates.str.match(r"^\d{2}/\d{2}/\d{4}$")]
        assert len(bad_format) == 0, (
            f"Malformed count_end_date values: {bad_format.tolist()}"
        )

        parsed = pd.to_datetime(dates, format="%d/%m/%Y", errors="coerce")
        too_early = parsed[parsed < pd.Timestamp("2026-05-01")]
        assert len(too_early) == 0, (
            f"{len(too_early)} date(s) before outbreak start (2026-05-01): "
            f"{too_early.dt.strftime('%d/%m/%Y').tolist()}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. KRAEMER DIVERGENCE TESTS  (cross-source validation)
# ─────────────────────────────────────────────────────────────────────────────

# Kraemer canonical zone name → extraction zone name (mirrors KRAEMER_NAME_MAP in the qmd)
_KRAEMER_ZONE_MAP = {"Mongbalu": "Mongbwalu", "Nyakunde": "Nyankunde"}

# Maximum tolerated absolute difference in cumulative confirmed cases per (zone, date) pair.
_MAX_CONFIRMED_DIVERGENCE = 5


class TestKraemerDivergence:
    """Flag when automated extraction diverges significantly from the INRB-UMIE reference data."""

    @pytest.fixture(scope="class")
    def kraemer_confirmed(self):
        csv = KRAEMER_LONG / "insp_sitrep__cumulative_confirmed_cases.csv"
        if not csv.exists():
            pytest.skip(
                "INRB-UMIE data not found — run: "
                "git submodule update --init Ebola_DRC_2026"
            )
        df = pd.read_csv(csv, dtype=str)
        df["zone"]      = df["nom"].map(lambda x: _KRAEMER_ZONE_MAP.get(x, x))
        df["date"]      = pd.to_datetime(df["date"], errors="coerce")
        df["confirmed"] = pd.to_numeric(df["cumulative_confirmed_cases"], errors="coerce")
        return df.dropna(subset=["date", "confirmed"])

    @pytest.fixture(scope="class")
    def extraction_confirmed(self):
        if not MASTER_CSV.exists():
            pytest.skip("master_combined_counts.csv not found — run extract_sitrep.py first")
        df = pd.read_csv(MASTER_CSV, dtype=str, keep_default_na=False)
        cum = df[
            (df["count_type"] == "Cumules") &
            (df["is_aggregate"].str.upper() != "TRUE") &
            (df["cases_confirmed"] != "")
        ].copy()
        cum["date"]      = pd.to_datetime(cum["count_end_date"], format="%d/%m/%Y", errors="coerce")
        cum["confirmed"] = pd.to_numeric(cum["cases_confirmed"], errors="coerce")
        return cum.dropna(subset=["date", "confirmed"])

    def test_kraemer_submodule_has_data(self, kraemer_confirmed):
        assert len(kraemer_confirmed) > 0, (
            "INRB-UMIE confirmed cases CSV is empty — submodule may not be initialised"
        )

    def test_extraction_zones_known_to_kraemer(self, kraemer_confirmed, extraction_confirmed):
        """Zones present in extraction but absent from INRB-UMIE may indicate a new area
        or a garbled zone name — emit a warning rather than a hard failure."""
        import warnings
        kraemer_zones    = set(kraemer_confirmed["zone"].unique())
        extraction_zones = set(
            extraction_confirmed["zone"][extraction_confirmed["zone"].str.strip() != ""].unique()
        )
        unknown = extraction_zones - kraemer_zones
        if unknown:
            warnings.warn(
                f"Extraction has zone(s) not (yet) in INRB-UMIE data — "
                f"may be new outbreak areas or name mismatches: {sorted(unknown)}",
                UserWarning,
                stacklevel=2,
            )

    def test_cumulative_confirmed_within_tolerance(
        self, kraemer_confirmed, extraction_confirmed
    ):
        """For matching (zone, date) pairs, divergence must not exceed the tolerance."""
        merged = pd.merge(
            extraction_confirmed[["zone", "date", "confirmed"]].rename(
                columns={"confirmed": "extraction"}
            ),
            kraemer_confirmed[["zone", "date", "confirmed"]].rename(
                columns={"confirmed": "kraemer"}
            ),
            on=["zone", "date"],
            how="inner",
        )
        if merged.empty:
            pytest.skip(
                "No overlapping (zone, date) pairs between extraction and INRB-UMIE — "
                "check that both datasets cover the same date range"
            )

        merged["delta"] = (merged["extraction"] - merged["kraemer"]).abs()
        violations = merged[merged["delta"] > _MAX_CONFIRMED_DIVERGENCE].sort_values(
            "delta", ascending=False
        )
        assert violations.empty, (
            f"{len(violations)} (zone, date) pair(s) exceed the divergence threshold "
            f"(±{_MAX_CONFIRMED_DIVERGENCE} confirmed cases):\n"
            + violations.assign(date=violations["date"].dt.strftime("%Y-%m-%d"))[
                ["zone", "date", "extraction", "kraemer", "delta"]
            ].to_string(index=False)
        )
