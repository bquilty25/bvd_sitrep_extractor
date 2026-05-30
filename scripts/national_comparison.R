# 05_national_comparison.R
# Cross-source comparison of national cumulative totals across three sources:
#   1. This repo's automated extraction (master_combined_counts.csv)
#   2. BVDOutbreakSize PDF-scanned reference (bvd_outbreak_size_national_reference.csv)
#   3. Kraemer lab national cumulative files (Ebola_DRC_2026 submodule)
# Outputs: outputs/national_comparison.csv

library(tidyverse)
library(here)
library(lubridate)

# ── 1. This repo: national aggregate Cumules rows ─────────────────────────────
df_raw <- read_csv(
  here::here("data", "processed", "master_combined_counts.csv"),
  col_types      = cols(.default = "c"),
  show_col_types = FALSE
)

ours <- df_raw |>
  filter(
    count_type == "Cumules",
    toupper(is_aggregate) == "TRUE",
    # National-level rows: zone=="Total" with no province, province=="Total"
    # with no zone, or both zone and province NA (un-labelled national total)
    (zone == "Total" | province == "Total" |
       (is.na(zone) & is.na(province)))
  ) |>
  mutate(
    date      = dmy(count_end_date),
    suspected = as.numeric(cases_suspect),
    confirmed = as.numeric(cases_confirmed),
    deaths    = coalesce(
      as.numeric(deaths_suspected),
      as.numeric(deaths_probable),
      as.numeric(deaths_confirmed)
    ),
    # Prefer province=="Total" rows (explicit sum row) > zone=="Total" >
    # unlabelled.  Within the same priority, prefer later sitrep versions by
    # taking the highest suspected count as a proxy for completeness.
    national_priority = case_when(
      province == "Total" ~ 3L,
      zone == "Total"     ~ 2L,
      TRUE                ~ 1L
    )
  ) |>
  group_by(date) |>
  slice_max(order_by = national_priority, n = 1, with_ties = FALSE) |>
  ungroup() |>
  transmute(
    date,
    sitrep_source,
    ours_suspected = suspected,
    ours_confirmed = confirmed,
    ours_deaths    = deaths
  )

# ── 2. BVDOutbreakSize PDF-scanned reference ───────────────────────────────────
bvd <- read_csv(
  here::here("data", "processed", "bvd_outbreak_size_national_reference.csv"),
  col_types      = cols(.default = "c"),
  show_col_types = FALSE
) |>
  mutate(
    date          = ymd(report_date),
    bvd_suspected = as.numeric(suspected_cases),
    bvd_confirmed = as.numeric(confirmed_cases),
    bvd_deaths    = as.numeric(suspected_deaths),
    bvd_sitrep    = paste0("SitRep ", sitrep)
  ) |>
  transmute(date, bvd_sitrep, bvd_suspected, bvd_confirmed, bvd_deaths, bvd_notes = notes)

# ── 3. Kraemer lab: national cumulative files ─────────────────────────────────
parse_kraemer_date <- function(x) {
  coalesce(dmy(x, quiet = TRUE), ymd(x, quiet = TRUE), mdy(x, quiet = TRUE))
}

read_kraemer_national <- function(metric) {
  read_csv(
    here::here(
      "Ebola_DRC_2026", "data", "insp_sitrep", "processed",
      paste0("insp_sitrep__national_cumulative_", metric, "__daily.csv")
    ),
    col_types      = cols(.default = "c"),
    show_col_types = FALSE
  ) |>
    mutate(date = parse_kraemer_date(date)) |>
    group_by(date) |>
    summarise(
      value = suppressWarnings(as.numeric(first(get(paste0("national_cumulative_", metric))))),
      .groups = "drop"
    )
}

kraemer_nat <- read_kraemer_national("suspected_cases") |>
  rename(kraemer_suspected = value) |>
  full_join(
    read_kraemer_national("confirmed_cases") |> rename(kraemer_confirmed = value),
    by = "date"
  ) |>
  full_join(
    read_kraemer_national("suspected_deaths") |> rename(kraemer_susp_deaths = value),
    by = "date"
  ) |>
  full_join(
    read_kraemer_national("confirmed_deaths") |> rename(kraemer_conf_deaths = value),
    by = "date"
  ) |>
  mutate(
    kraemer_deaths = coalesce(kraemer_susp_deaths, 0) + coalesce(kraemer_conf_deaths, 0),
    kraemer_deaths = if_else(
      is.na(kraemer_susp_deaths) & is.na(kraemer_conf_deaths),
      NA_real_, kraemer_deaths
    )
  ) |>
  select(date, kraemer_suspected, kraemer_confirmed, kraemer_deaths)

# ── 4. Join all three sources on date ────────────────────────────────────────
comparison <- ours |>
  full_join(bvd,        by = "date") |>
  full_join(kraemer_nat, by = "date") |>
  arrange(date) |>
  mutate(
    # Flag cells where sources disagree (ignoring NAs)
    flag_suspected = case_when(
      !is.na(ours_suspected) & !is.na(bvd_suspected)    & ours_suspected != bvd_suspected    ~ "ours≠BVD",
      !is.na(ours_suspected) & !is.na(kraemer_suspected) & ours_suspected != kraemer_suspected ~ "ours≠Kraemer",
      TRUE ~ NA_character_
    ),
    flag_confirmed = case_when(
      !is.na(ours_confirmed) & !is.na(bvd_confirmed)    & ours_confirmed != bvd_confirmed    ~ "ours≠BVD",
      !is.na(ours_confirmed) & !is.na(kraemer_confirmed) & ours_confirmed != kraemer_confirmed ~ "ours≠Kraemer",
      TRUE ~ NA_character_
    ),
    flag_deaths = case_when(
      !is.na(ours_deaths) & !is.na(bvd_deaths)    & ours_deaths != bvd_deaths    ~ "ours≠BVD",
      !is.na(ours_deaths) & !is.na(kraemer_deaths) & ours_deaths != kraemer_deaths ~ "ours≠Kraemer",
      TRUE ~ NA_character_
    )
  )

# ── 5. Output ─────────────────────────────────────────────────────────────────
write_csv(comparison, here::here("data", "processed", "national_comparison.csv"))
message("Wrote ", nrow(comparison), " rows to data/processed/national_comparison.csv")
print(comparison)
