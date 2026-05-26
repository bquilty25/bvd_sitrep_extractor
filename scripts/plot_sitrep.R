if (!requireNamespace("pacman", quietly = TRUE)) install.packages("pacman")
pacman::p_load(tidyverse, lubridate, here, scales, ggrepel)

# ── Load ───────────────────────────────────────────────────────────────────────
df_raw <- read_csv(
  here::here("outputs", "master_combined_counts.csv"),
  col_types = cols(.default = "c")
)

df <- df_raw |>
  mutate(
    date      = dmy(count_end_date),
    suspect   = as.numeric(cases_suspect),
    probable  = as.numeric(cases_probable),
    confirmed = as.numeric(cases_confirmed),
    deaths    = as.numeric(deaths_suspected),
    is_agg    = (is_aggregate == "TRUE"),
    # Merge suspect + probable into one "Unconfirmed" series. Some sitreps
    # populate only one field, so this avoids disconnected / isolated dots.
    unconfirmed = coalesce(suspect, 0L) + coalesce(probable, 0L)
  )

# ── Constants ──────────────────────────────────────────────────────────────────
# Zones ordered by epidemic burden (Ituri first, then Nord-Kivu)
ZONE_LEVELS <- c(
  "Mongbwalu", "Rwampara", "Bunia", "Nyankunde", "Bambu",
  "Butembo", "Katwa", "Goma"
)

# Colorblind-safe palette — Wong (2011)
PAL <- c(
  "Suspect/probable cases" = "#56B4E9",  # sky blue
  "Confirmed cases"        = "#D55E00",  # vermillion
  "Suspected deaths"       = "#404040"   # dark grey
)

# Force integer axis breaks (suppresses decimal labels on low-count panels)
int_breaks <- function(x) unique(floor(pretty(x)))

# ── Shared theme ───────────────────────────────────────────────────────────────
theme_sitrep <- function(base_size = 11) {
  theme_minimal(base_size = base_size) %+replace%
    theme(
      plot.title       = element_text(face = "bold", size = base_size + 2, hjust = 0),
      plot.subtitle    = element_text(size = base_size - 1, colour = "grey45", hjust = 0,
                                      margin = margin(b = 8)),
      plot.caption     = element_text(size = base_size - 3, colour = "grey55", hjust = 1,
                                      margin = margin(t = 8)),
      plot.margin      = margin(12, 16, 8, 12),
      panel.grid.minor = element_blank(),
      panel.grid.major = element_line(colour = "grey92"),
      legend.position  = "bottom",
      legend.key.size  = unit(0.75, "lines"),
      legend.text      = element_text(size = base_size - 1),
      strip.text       = element_text(face = "bold", size = base_size - 1,
                                      margin = margin(b = 4)),
      axis.title       = element_text(size = base_size - 1, colour = "grey35"),
      axis.text        = element_text(size = base_size - 2, colour = "grey25")
    )
}

# ── Derived subsets ────────────────────────────────────────────────────────────
totals <- df |>
  filter(count_type == "Cumules", is_agg, zone == "Total") |>
  group_by(date) |>
  slice_max(order_by = unconfirmed, n = 1, with_ties = FALSE) |>
  ungroup() |>
  arrange(date)

# Zone cumulative — one row per (date, zone); enforce monotonicity with cummax
zone_ts <- df |>
  filter(count_type == "Cumules", !is_agg, zone %in% ZONE_LEVELS) |>
  group_by(date, zone, province) |>
  slice_max(order_by = unconfirmed, n = 1, with_ties = FALSE) |>
  ungroup() |>
  mutate(zone = factor(zone, levels = ZONE_LEVELS)) |>
  arrange(zone, date) |>
  group_by(zone) |>
  mutate(
    unconfirmed = cummax(replace_na(unconfirmed, 0L)),
    confirmed   = cummax(replace_na(confirmed,   0L)),
    deaths      = cummax(replace_na(deaths,      0L))
  ) |>
  ungroup()

# Zone new cases — one row per (date, zone)
zone_new <- df |>
  filter(count_type == "Nouveaux", !is_agg, zone %in% ZONE_LEVELS) |>
  group_by(date, zone, province) |>
  slice_max(order_by = unconfirmed, n = 1, with_ties = FALSE) |>
  ungroup() |>
  mutate(zone = factor(zone, levels = ZONE_LEVELS)) |>
  arrange(zone, date)

# Daily new cases — total row
daily_new <- df |>
  filter(count_type == "Nouveaux", is_agg, zone == "Total") |>
  group_by(date) |>
  slice_max(order_by = unconfirmed, n = 1, with_ties = FALSE) |>
  ungroup() |>
  arrange(date)

# Ordered facet label: "Zone (Province)"
make_zone_labels <- function(dat) {
  dat |>
    mutate(
      zone_label = paste0(zone, " (", province, ")"),
      zone_label = factor(zone_label,
        levels = dat |>
          distinct(zone, province) |>
          arrange(zone) |>
          mutate(lbl = paste0(zone, " (", province, ")")) |>
          pull(lbl)
      )
    )
}

# ── Plot 1: Cumulative epidemic curve (totals) ─────────────────────────────────
p1_dat <- totals |>
  select(date,
         `Suspect/probable cases` = unconfirmed,
         `Confirmed cases`        = confirmed,
         `Suspected deaths`       = deaths) |>
  pivot_longer(-date, names_to = "category", values_to = "count") |>
  mutate(category = factor(category, levels = names(PAL)))

# Last non-NA value per series — used for end-of-line labels
p1_last <- p1_dat |>
  filter(!is.na(count)) |>
  group_by(category) |>
  slice_max(date, n = 1) |>
  ungroup()

p1 <- ggplot(p1_dat, aes(x = date, y = count, colour = category, group = category)) +
  geom_line(linewidth = 1.1, na.rm = TRUE) +
  geom_point(size = 2.8, na.rm = TRUE) +
  geom_label_repel(
    data           = p1_last,
    aes(label      = count),
    size           = 3.5,
    nudge_x        = 1.5,
    direction      = "y",
    hjust          = 0,
    segment.colour = "grey70",
    segment.size   = 0.3,
    show.legend    = FALSE,
    na.rm          = TRUE
  ) +
  scale_colour_manual(values = PAL) +
  scale_x_date(
    date_labels = "%d %b",
    date_breaks = "2 days",
    expand      = expansion(add = c(0, 4))
  ) +
  scale_y_continuous(breaks = int_breaks, expand = expansion(mult = c(0, 0.08))) +
  labs(
    title    = "MVE DRC \u2014 Cumulative epidemic curve",
    subtitle = "Total counts across all health zones",
    x = NULL, y = "Cumulative count", colour = NULL,
    caption  = "Source: INSP DRC situation reports"
  ) +
  theme_sitrep(base_size = 12)

# ── Plot 2: Daily new cases (totals) ──────────────────────────────────────────
p2 <- daily_new |>
  select(date,
         `Suspect/probable cases` = unconfirmed,
         `Confirmed cases`        = confirmed) |>
  pivot_longer(-date, names_to = "category", values_to = "count") |>
  mutate(category = factor(category, levels = c("Suspect/probable cases", "Confirmed cases"))) |>
  ggplot(aes(x = date, y = count, fill = category)) +
  geom_col(position = "dodge", width = 0.3, na.rm = TRUE) +
  scale_fill_manual(values = PAL) +
  scale_x_date(date_labels = "%d %b", date_breaks = "1 day") +
  scale_y_continuous(breaks = int_breaks, expand = expansion(mult = c(0, 0.1))) +
  labs(
    title    = "MVE DRC \u2014 Daily new cases",
    subtitle = "Total reported per situation report",
    x = NULL, y = "New cases", fill = NULL,
    caption  = "Source: INSP DRC situation reports"
  ) +
  theme_sitrep(base_size = 12)

# ── Plot 3: Per-zone cumulative time series ────────────────────────────────────
zone_ts_long <- zone_ts |>
  make_zone_labels() |>
  select(date, zone_label,
         `Suspect/probable cases` = unconfirmed,
         `Confirmed cases`        = confirmed,
         `Suspected deaths`       = deaths) |>
  pivot_longer(
    cols      = c(`Suspect/probable cases`, `Confirmed cases`, `Suspected deaths`),
    names_to  = "category",
    values_to = "count"
  ) |>
  mutate(category = factor(category, levels = names(PAL)))

p3 <- zone_ts_long |>
  ggplot(aes(x = date, y = count, colour = category, group = category)) +
  geom_line(linewidth = 0.9, na.rm = TRUE) +
  geom_point(size = 2.2, na.rm = TRUE) +
  scale_colour_manual(values = PAL) +
  scale_x_date(date_labels = "%d %b", date_breaks = "1 day") +
  scale_y_continuous(breaks = int_breaks, expand = expansion(mult = c(0, 0.15))) +
  facet_wrap(~ zone_label, scales = "free_y", ncol = 2) +
  labs(
    title    = "MVE DRC \u2014 Cumulative cases by health zone",
    subtitle = "Each panel shows one health zone; y-axis is free.",
    x = NULL, y = "Cumulative count", colour = NULL,
    caption  = "Source: INSP DRC situation reports"
  ) +
  theme_sitrep(base_size = 10) +
  theme(axis.text.x = element_text(size = 8, angle = 45, hjust = 1))

# ── Plot 4: Per-zone daily new cases ──────────────────────────────────────────
zone_new_long <- zone_new |>
  make_zone_labels() |>
  select(date, zone_label,
         `Suspect/probable cases` = unconfirmed,
         `Confirmed cases`        = confirmed) |>
  pivot_longer(
    cols      = c(`Suspect/probable cases`, `Confirmed cases`),
    names_to  = "category",
    values_to = "count"
  ) |>
  mutate(category = factor(category, levels = c("Suspect/probable cases", "Confirmed cases")))

p4 <- zone_new_long |>
  ggplot(aes(x = date, y = count, fill = category)) +
  geom_col(position = "dodge", width = 0.4, na.rm = TRUE) +
  scale_fill_manual(values = PAL) +
  scale_x_date(date_labels = "%d %b", date_breaks = "1 day") +
  scale_y_continuous(breaks = int_breaks, expand = expansion(mult = c(0, 0.15))) +
  facet_wrap(~ zone_label, scales = "free_y", ncol = 2) +
  labs(
    title    = "MVE DRC \u2014 Daily new cases by health zone",
    subtitle = "Per situation report; y-axis is free.",
    x = NULL, y = "New cases", fill = NULL,
    caption  = "Source: INSP DRC situation reports"
  ) +
  theme_sitrep(base_size = 10) +
  theme(axis.text.x = element_text(size = 8, angle = 45, hjust = 1))

# ── Save ───────────────────────────────────────────────────────────────────────
out_dir <- here::here("outputs", "plots")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

ggsave(file.path(out_dir, "plot_01_cumulative_curve.png"),
       p1, width = 8, height = 5, dpi = 300)
ggsave(file.path(out_dir, "plot_02_daily_new_cases.png"),
       p2, width = 7, height = 4, dpi = 300)
ggsave(file.path(out_dir, "plot_03_zone_timeseries_cumulative.png"),
       p3, width = 10, height = 11, dpi = 300)
ggsave(file.path(out_dir, "plot_04_zone_timeseries_new_cases.png"),
       p4, width = 10, height = 11, dpi = 300)

message("Plots saved to outputs/plots/")
