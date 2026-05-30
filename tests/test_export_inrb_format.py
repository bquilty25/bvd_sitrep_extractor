"""
Unit and integration tests for scripts/export_inrb_format.py.

Run:
  pytest tests/test_export_inrb_format.py -v
"""

import csv
import re
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from export_inrb_format import (
    _parse_date,
    _to_nd,
    _write_metric,
    ZONE_NOUVEAUX,
    ZONE_CUMULES,
    NATIONAL_CUMULES,
    RESPONSE_METRICS,
    POE_METRICS,
)

# Build a flat list of all expected output filenames (index 1 for most, index 2 for response/poe)
_ALL_METRIC_TUPLES: list[tuple] = [
    *ZONE_NOUVEAUX, *ZONE_CUMULES, *NATIONAL_CUMULES,
]
_RESPONSE_AND_POE_TUPLES: list[tuple] = [*RESPONSE_METRICS, *POE_METRICS]

ALL_EXPECTED_FILENAMES: list[str] = (
    [t[1] for t in _ALL_METRIC_TUPLES]
    + [t[2] for t in _RESPONSE_AND_POE_TUPLES]
)

DATA_PROCESSED = Path(__file__).parent.parent / "data" / "processed"

# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — pure helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestParseDate:
    def test_standard_ddmmyyyy(self):
        assert _parse_date("18/05/2026") == "2026-05-18"

    def test_single_digit_day(self):
        assert _parse_date("5/05/2026") == "2026-05-05"

    def test_single_digit_month(self):
        assert _parse_date("18/5/2026") == "2026-05-18"

    def test_empty_string_returns_empty(self):
        assert _parse_date("") == ""

    def test_none_returns_empty(self):
        assert _parse_date(None) == ""

    def test_iso_already_parses(self):
        # ISO dates are parseable by pd.to_datetime fallback path
        assert _parse_date("2026-05-18") == "2026-05-18"

    def test_invalid_returns_stripped_string(self):
        # when both parse attempts fail, the raw stripped string is returned
        assert _parse_date("not-a-date") == "not-a-date"


class TestToNd:
    def test_empty_string_becomes_nd(self):
        assert _to_nd("") == "ND"

    def test_nan_becomes_nd(self):
        import math
        assert _to_nd(float("nan")) == "ND"

    def test_none_becomes_nd(self):
        assert _to_nd(None) == "ND"

    def test_zero_preserved(self):
        # returns the value unchanged (not stringified)
        assert _to_nd(0) == 0

    def test_zero_str_preserved(self):
        assert _to_nd("0") == "0"

    def test_integer_preserved(self):
        assert _to_nd(42) == 42

    def test_string_number_preserved(self):
        assert _to_nd("123") == "123"

    def test_nd_sentinel_stays_nd(self):
        assert _to_nd("ND") == "ND"


class TestWriteMetric:
    def test_creates_file_with_correct_columns(self, tmp_path):
        nom = pd.Series(["ZoneA", "ZoneB"])
        date = pd.Series(["2026-05-18", "2026-05-18"])
        metric = pd.Series([5, 10])
        out = tmp_path / "test_metric.csv"
        _write_metric(nom, date, metric, "my_metric", out)
        df = pd.read_csv(out)
        assert list(df.columns) == ["nom", "date", "my_metric"]

    def test_sorted_by_date_then_nom(self, tmp_path):
        nom = pd.Series(["ZoneB", "ZoneA", "ZoneB", "ZoneA"])
        date = pd.Series(["2026-05-19", "2026-05-18", "2026-05-18", "2026-05-19"])
        metric = pd.Series([1, 2, 3, 4])
        out = tmp_path / "sorted.csv"
        _write_metric(nom, date, metric, "val", out)
        df = pd.read_csv(out)
        assert list(df["date"]) == ["2026-05-18", "2026-05-18", "2026-05-19", "2026-05-19"]
        assert list(df["nom"]) == ["ZoneA", "ZoneB", "ZoneA", "ZoneB"]

    def test_row_count_matches_input(self, tmp_path):
        nom = pd.Series(["Z1", "Z2", "Z3"])
        date = pd.Series(["2026-05-18", "2026-05-18", "2026-05-18"])
        metric = pd.Series([1, 2, 3])
        out = tmp_path / "count.csv"
        _write_metric(nom, date, metric, "cases", out)
        df = pd.read_csv(out)
        assert len(df) == 3


# ─────────────────────────────────────────────────────────────────────────────
# Integration tests — validate files in data/processed/
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def processed_files():
    """Return {stem: DataFrame} for all CSVs in data/processed/."""
    if not DATA_PROCESSED.exists():
        pytest.skip("data/processed/ not found — run export_inrb_format.py first")
    return {
        p.name: pd.read_csv(p)
        for p in sorted(DATA_PROCESSED.glob("insp_sitrep__*__daily.csv"))
    }


class TestProcessedFileCount:
    def test_expected_number_of_files(self, processed_files):
        n = len(ALL_EXPECTED_FILENAMES)
        assert len(processed_files) == n, (
            f"Expected {n} files, found {len(processed_files)}"
        )

    def test_all_expected_filenames_present(self, processed_files):
        expected = set(ALL_EXPECTED_FILENAMES)
        actual = set(processed_files.keys())
        missing = expected - actual
        assert not missing, f"Missing files: {missing}"


class TestProcessedFileSchema:
    @pytest.mark.parametrize("filename", ALL_EXPECTED_FILENAMES)
    def test_has_three_columns(self, processed_files, filename):
        df = processed_files[filename]
        assert len(df.columns) == 3, f"{filename}: expected 3 columns, got {list(df.columns)}"

    @pytest.mark.parametrize("filename", ALL_EXPECTED_FILENAMES)
    def test_first_two_columns_are_nom_date(self, processed_files, filename):
        df = processed_files[filename]
        assert df.columns[0] == "nom", f"{filename}: first column is '{df.columns[0]}', expected 'nom'"
        assert df.columns[1] == "date", f"{filename}: second column is '{df.columns[1]}', expected 'date'"

    @pytest.mark.parametrize("filename", ALL_EXPECTED_FILENAMES)
    def test_dates_are_iso_format(self, processed_files, filename):
        df = processed_files[filename]
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        non_iso = df["date"].dropna().loc[lambda s: ~s.astype(str).str.match(iso_re)]
        assert len(non_iso) == 0, f"{filename}: non-ISO dates: {non_iso.tolist()}"

    @pytest.mark.parametrize("filename", ALL_EXPECTED_FILENAMES)
    def test_has_rows(self, processed_files, filename):
        df = processed_files[filename]
        assert len(df) > 0, f"{filename}: no rows"

    @pytest.mark.parametrize("filename", ALL_EXPECTED_FILENAMES)
    def test_nom_not_empty(self, processed_files, filename):
        df = processed_files[filename]
        empty_nom = df["nom"].isna() | (df["nom"].astype(str).str.strip() == "")
        assert not empty_nom.any(), f"{filename}: has rows with empty 'nom'"


class TestProcessedFileContent:
    def test_zone_level_files_have_multiple_zones(self, processed_files):
        # zone-level files should have at least 2 distinct zone names
        # exclude national_ files (single National row) and poe files (national aggregate only)
        poe_filenames = {t[2] for t in POE_METRICS}
        zone_files = [
            fn for fn in processed_files
            if not fn.startswith("insp_sitrep__national_")
            and fn not in poe_filenames
        ]
        for fn in zone_files:
            df = processed_files[fn]
            n_zones = df["nom"].nunique()
            assert n_zones > 1, f"{fn}: only {n_zones} zone(s) — expected multiple"

    def test_national_files_have_only_national_nom(self, processed_files):
        nat_files = [
            fn for fn in processed_files
            if fn.startswith("insp_sitrep__national_")
        ]
        for fn in nat_files:
            df = processed_files[fn]
            non_nat = df.loc[df["nom"].str.lower() != "national", "nom"].unique()
            assert len(non_nat) == 0, f"{fn}: unexpected non-National noms: {non_nat}"

    def test_no_duplicate_nom_date_pairs(self, processed_files):
        for fn, df in processed_files.items():
            dupes = df.duplicated(subset=["nom", "date"])
            assert not dupes.any(), (
                f"{fn}: duplicate nom+date pairs: "
                f"{df.loc[dupes, ['nom','date']].values.tolist()}"
            )

    def test_dates_in_expected_range(self, processed_files):
        for fn, df in processed_files.items():
            dates = pd.to_datetime(df["date"], errors="coerce")
            assert dates.notna().all(), f"{fn}: unparseable dates"
            assert (dates >= "2026-05-01").all(), f"{fn}: dates before outbreak start"
            assert (dates <= "2027-01-01").all(), f"{fn}: dates unexpectedly far in future"
