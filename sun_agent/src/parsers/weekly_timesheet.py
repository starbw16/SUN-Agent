"""
Parser for Weekly_Time_Sheet_Report.
Shape: repeated per-employee blocks, each with a header row, Time In/Out rows,
optional break rows, a Totals row, and summary rows.
Produces: provider_timesheet records (one per provider per day worked).
"""
import re
import pandas as pd
from datetime import datetime, timedelta

from .base_parser import BaseParser, ParseResult
from ..parsers.header_detector import extract_preamble_metadata
from ..normalization.normalizer import normalize_date

DATE_COL_RE = re.compile(r"(\d{1,2})-(\d{1,2})")   # "4-20" in "Monday  4-20"
TOTAL_RE = re.compile(r"^(\d{1,2}):(\d{2})\s*$")   # "03:36"
TIME_RE = re.compile(r"(\d{1,2}:\d{2})\s*(AM|PM)", re.I)


def _parse_hours(raw: str) -> float | None:
    """'03:36' → 3.6 hours. Returns None if blank/zero."""
    if not raw or str(raw).strip() in ("", "nan", "00:00"):
        return None
    m = TOTAL_RE.match(str(raw).strip())
    if m:
        h, mn = int(m.group(1)), int(m.group(2))
        return round(h + mn / 60, 4)
    return None


def _parse_time(raw: str) -> str | None:
    """'03:32  PM' → '03:32 PM'. Returns None if blank/zero."""
    if not raw or str(raw).strip() in ("", "nan", "00:00"):
        return None
    m = TIME_RE.search(str(raw).strip())
    if m:
        return f"{m.group(1)} {m.group(2).upper()}"
    return None


def _col_date(col_header: str, report_monday: datetime) -> str | None:
    """'Tuesday  4-21' → '2026-04-21' using the report's Monday as anchor."""
    m = DATE_COL_RE.search(str(col_header))
    if not m:
        return None
    month, day = int(m.group(1)), int(m.group(2))
    # Walk Mon→Sun from report_monday to find the matching month/day
    for offset in range(7):
        d = report_monday + timedelta(days=offset)
        if d.month == month and d.day == day:
            return d.strftime("%Y-%m-%d")
    return None


class WeeklyTimesheetParser(BaseParser):
    report_type = "weekly_timesheet"

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

        # Preamble: store name, report date (= week's Monday), date generated
        metadata = extract_preamble_metadata(raw_df, max_rows=5)
        report_date_str = normalize_date(
            metadata.get("report_date") or metadata.get("report_period") or metadata.get("report_title")
        )

        try:
            report_monday = datetime.strptime(report_date_str, "%Y-%m-%d")
        except (ValueError, TypeError):
            return ParseResult(
                report_type=self.report_type, store_name_raw=metadata.get("store_name"),
                report_start_date=None, report_end_date=None, generated_at=None,
                file_hash=file_hash, source_filename=self.filepath.name,
                error=f"Could not parse report date: {report_date_str}",
            )

        rows_out = []
        data = [[str(v).strip() for v in raw_df.iloc[i]] for i in range(len(raw_df))]

        i = 0
        while i < len(data):
            row = data[i]

            # Detect employee header row: col0 non-empty, col1 == "Time Block"
            if len(row) >= 2 and row[1].lower() == "time block" and row[0].lower() == "employee name":
                # Build date map: col_index → ISO date
                date_map = {}
                for ci in range(2, len(row)):
                    d = _col_date(row[ci], report_monday)
                    if d:
                        date_map[ci] = d

                i += 1
                first_name = last_name = ""
                time_in_by_date: dict[str, str] = {}
                time_out_by_date: dict[str, str] = {}
                totals_by_date: dict[str, float] = {}

                # Consume rows until next employee header or end
                while i < len(data):
                    r = data[i]
                    label = r[1].lower() if len(r) > 1 else ""

                    if label == "time block":
                        break  # next employee block

                    # Capture last name from any row col0 (break rows carry it)
                    if r[0] and r[0].lower() not in ("nan", "") and first_name and not last_name:
                        last_name = r[0]

                    if label == "time in":
                        first_name = r[0] if r[0] and r[0].lower() != "nan" else first_name
                        for ci, dt in date_map.items():
                            t = _parse_time(r[ci] if ci < len(r) else "")
                            if t:
                                time_in_by_date[dt] = t

                    elif label == "time out":
                        for ci, dt in date_map.items():
                            t = _parse_time(r[ci] if ci < len(r) else "")
                            if t:
                                time_out_by_date[dt] = t

                    elif label.startswith("totals"):
                        for ci, dt in date_map.items():
                            h = _parse_hours(r[ci] if ci < len(r) else "")
                            if h is not None and h > 0:
                                totals_by_date[dt] = h

                        # Emit records for this employee
                        provider_name = f"{first_name} {last_name}".strip()
                        for dt, hours in totals_by_date.items():
                            tin = time_in_by_date.get(dt)
                            tout = time_out_by_date.get(dt)
                            is_complete = tout is not None
                            appt_slots = None  # filled by utilization engine via config
                            rows_out.append({
                                "_record_type": "provider_timesheet",
                                "provider_name_raw": provider_name,
                                "provider_name_normalized": provider_name.lower(),
                                "work_date": dt,
                                "time_in": tin,
                                "time_out": tout,
                                "hours_worked": hours,
                                "is_complete": 1 if is_complete else 0,
                                "source_report": self.report_type,
                            })

                    i += 1
                continue  # don't increment again

            i += 1

        week_end = (report_monday + timedelta(days=6)).strftime("%Y-%m-%d")
        return ParseResult(
            report_type=self.report_type,
            store_name_raw=metadata.get("store_name"),
            report_start_date=report_date_str,
            report_end_date=week_end,
            generated_at=metadata.get("generated_at"),
            file_hash=file_hash,
            source_filename=self.filepath.name,
            rows=rows_out,
            row_count_raw=len(rows_out),
        )
