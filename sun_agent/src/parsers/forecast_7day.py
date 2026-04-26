"""
Parser for 7_Day_Appointment_Forecast_Report.
Shape: cross-tab — dates across columns, booking-source rows beneath a two-row header.
Produces: forecast_daily_channel records (tidy long form).
"""
import re
import pandas as pd

from .base_parser import BaseParser, ParseResult
from ..normalization.normalizer import normalize_date, is_blank_column

BOOKING_CHANNELS = [
    "booked by receptionist",
    "online booked",
    "google reserve",
    "walk in",
    "walk-in",
]

DATE_RE = re.compile(r"\d{1,2}/\d{1,2}/\d{2,4}")


class Forecast7DayParser(BaseParser):
    report_type = "forecast_7day"

    def parse(self) -> ParseResult:
        file_hash = self._file_hash()
        try:
            raw_df = self._load_raw()
        except Exception as exc:
            return ParseResult(
                report_type=self.report_type, store_name_raw=None,
                report_start_date=None, report_end_date=None, generated_at=None,
                file_hash=file_hash, source_filename=self.filepath.name, error=str(exc),
            )

        # Scan for the date header row (row where multiple cells look like dates)
        date_row_idx = None
        for idx in range(min(20, len(raw_df))):
            row_vals = [str(v).strip() for v in raw_df.iloc[idx]]
            date_hits = sum(1 for v in row_vals if DATE_RE.search(v))
            if date_hits >= 2:
                date_row_idx = idx
                break

        if date_row_idx is None:
            return ParseResult(
                report_type=self.report_type, store_name_raw=None,
                report_start_date=None, report_end_date=None, generated_at=None,
                file_hash=file_hash, source_filename=self.filepath.name,
                error="Could not detect date header row in forecast report",
            )

        date_row = [str(v).strip() for v in raw_df.iloc[date_row_idx]]
        # Subheader row immediately follows
        sub_row_idx = date_row_idx + 1
        sub_row = [str(v).strip() for v in raw_df.iloc[sub_row_idx]] if sub_row_idx < len(raw_df) else []

        # Collect dates in order of appearance (cols 1+)
        dates_in_order = [v for v in date_row[1:] if DATE_RE.search(v)]

        # Build column index: (date_label, channel_label) -> col_index
        # Each date occupies a group of N consecutive channel columns in sub_row.
        # Detect channels_per_date by counting unique channel labels before repetition.
        channels_seen = []
        for v in sub_row[1:]:
            lv = v.lower().strip()
            if any(kw in lv for kw in BOOKING_CHANNELS):
                if lv in channels_seen:
                    break
                channels_seen.append(lv)
        channels_per_date = len(channels_seen) if channels_seen else 3

        col_index = {}
        data_col_offset = 0
        for i, val in enumerate(sub_row):
            if i == 0:
                continue
            channel = val.lower().strip()
            if not any(kw in channel for kw in BOOKING_CHANNELS):
                continue
            date_idx = data_col_offset // channels_per_date
            if date_idx < len(dates_in_order):
                col_index[(dates_in_order[date_idx], channel)] = i
            data_col_offset += 1

        # Data rows start after subheader
        rows = []
        for row_idx in range(sub_row_idx + 1, len(raw_df)):
            row_vals = [str(v).strip() for v in raw_df.iloc[row_idx]]
            row_text = " ".join(v for v in row_vals if v and v.lower() != "nan")
            if not row_text:
                continue

            for (date_label, channel), col_i in col_index.items():
                raw_count = row_vals[col_i] if col_i < len(row_vals) else ""
                try:
                    count = int(float(raw_count.replace(",", ""))) if raw_count and raw_count.lower() != "nan" else 0
                except ValueError:
                    count = 0

                rows.append({
                    "_record_type": "forecast_daily_channel",
                    "forecast_date_raw": date_label,
                    "forecast_date": normalize_date(date_label),
                    "booking_channel": channel,
                    "booking_count": count,
                    "source_report": self.report_type,
                })

        from ..parsers.header_detector import extract_preamble_metadata
        metadata = extract_preamble_metadata(raw_df, max_rows=date_row_idx)

        return self._make_result(self.report_type, metadata, file_hash, rows, len(rows))
