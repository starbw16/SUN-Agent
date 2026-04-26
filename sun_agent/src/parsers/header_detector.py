"""
Detects the actual data header row in Salon Ultimate exports, skipping preamble/metadata rows.
Returns (header_row_index, metadata_dict).
"""
import re
import pandas as pd
from typing import Optional

# Anchor tokens that reliably appear in data header rows for each report type
HEADER_ANCHORS = {
    "clients_with_service": {"client name", "service category", "service description"},
    "appt_conf_list": {"appointment time", "stylist code", "client name"},
    "stylist_daily_schedule": {"time", "client + service", "status"},
    "cancel_no_show": {"client name", "total cancel/no shows"},
    "appointment_audits": {"app date", "who booked", "client name", "action"},
    "forecast_7day": {"booked by receptionist", "online booked", "google reserve"},
}

# Metadata keys found in preamble rows
METADATA_PATTERNS = {
    "store_name": re.compile(r"(?:store|location|salon)\s*[:\-]?\s*(.+)", re.I),
    "report_date": re.compile(r"^report\s+date\s*[:\-]?\s*(.+)", re.I),
    "report_period": re.compile(r"(?:date range|period|from)\s*[:\-]?\s*(.+)", re.I),
    "generated_at": re.compile(r"(?:generated|printed|run date)\s*[:\-]?\s*(.+)", re.I),
    "report_title": re.compile(r"(?:report|title)\s*[:\-]?\s*(.+)", re.I),
}


def extract_preamble_metadata(df: pd.DataFrame, max_rows: int = 10) -> dict:
    """
    Extract store metadata from preamble rows.
    Handles both single-cell "Store name: Grand Rapids" and two-cell ["Store name:", "Grand Rapids"] formats.
    """
    metadata = {}
    for idx in range(min(max_rows, len(df))):
        row_cells_raw = [str(v).strip() for v in df.iloc[idx]]
        for i, cell in enumerate(row_cells_raw):
            if not cell or cell.lower() in ("nan", ""):
                continue
            for key, pattern in METADATA_PATTERNS.items():
                if key in metadata:
                    continue
                m = pattern.search(cell)
                if m:
                    captured = m.group(1).strip()
                    if captured.endswith(":") or (len(captured) < 8 and ":" in captured):
                        for j in range(i + 1, len(row_cells_raw)):
                            nxt = row_cells_raw[j]
                            if nxt and nxt.lower() not in ("nan", ""):
                                metadata[key] = nxt
                                break
                    else:
                        metadata[key] = captured
    return metadata


def detect_header_row(df: pd.DataFrame, report_type: Optional[str] = None) -> tuple:
    """
    Scan rows top-to-bottom. Return (header_row_index, metadata_dict).
    header_row_index is the 0-based row index of the actual column header.
    """
    metadata = extract_preamble_metadata(df, max_rows=30)
    anchors = set()
    if report_type and report_type in HEADER_ANCHORS:
        anchors = HEADER_ANCHORS[report_type]

    for idx in range(min(30, len(df))):
        row_values = [str(v).strip().lower() for v in df.iloc[idx] if str(v).strip() not in ("", "nan")]

        # Check if this row looks like a header
        if anchors:
            row_set = set(row_values)
            matched = anchors & row_set
            if len(matched) >= 2:
                return idx, metadata
        else:
            # Generic: row has 3+ non-empty distinct string cells, none look like data
            non_empty = [v for v in row_values if v and v != "nan"]
            if len(non_empty) >= 3 and _looks_like_header(non_empty):
                return idx, metadata

    return 0, metadata


def _looks_like_header(values: list) -> bool:
    """Heuristic: headers are mostly text, not dates or pure numbers."""
    text_count = sum(1 for v in values if not re.match(r"^\d[\d/\-:.]+$", v))
    return text_count / len(values) >= 0.6


def detect_report_type(filename: str, df: pd.DataFrame) -> str:
    """
    Classify report type from filename first, then fall back to content sniffing.
    Returns a canonical type string.
    """
    fname = filename.lower().replace(" ", "_")

    type_map = {
        "clients_with_service": "clients_with_service",
        "appt_conf_list": "appt_conf_list",
        "stylist_daily_schedule": "stylist_daily_schedule",
        "cancel_no_show": "cancel_no_show",
        "appointment_audits": "appointment_audits",
        "7_day_appointment_forecast": "forecast_7day",
        "rebooking_clients": "rebooking_clients",
        "frequency_of_visit": "frequency_of_visit",
        "client_demographics": "client_demographics",
        "stylist_appt_listing": "stylist_appt_listing",
        "wait_time_detail": "wait_time_detail",
        "weekly_time_sheet": "weekly_timesheet",
        "weekly_timesheet": "weekly_timesheet",
        "time_sheet": "weekly_timesheet",
    }

    for key, val in type_map.items():
        if key in fname:
            return val

    # Content sniff: look at first 10 rows for anchor tokens
    sample_text = " ".join(
        str(v).lower() for row in df.head(10).values for v in row if str(v).strip() not in ("", "nan")
    )
    for type_name, anchors in HEADER_ANCHORS.items():
        if sum(1 for a in anchors if a in sample_text) >= 2:
            return type_name

    return "unknown"
