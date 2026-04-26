"""
Parser for Stylist_Daily_Schedule report.
Shape: repeated provider sections; each section has a provider header then slot rows.
Produces: provider_schedule_slot records.
"""
import re
import pandas as pd

from .base_parser import BaseParser, ParseResult
from ..parsers.header_detector import detect_header_row
from ..normalization.normalizer import normalize_headers, is_blank_column, normalize_name

# Matches "Provider Daily Schedule for Name on Date" or "Provider: Name" etc.
PROVIDER_HEADER_RE = re.compile(r"(?:provider|stylist)\s*[:\-]?\s*(.+)", re.I)
# Extracts provider name and schedule date from "Provider Daily Schedule for NAME on DATE"
SCHEDULE_FOR_RE = re.compile(
    r"provider\s+daily\s+schedule\s+for\s+(.+?)\s+on\s+(.+)", re.I
)
# Detects a bare time cell like "3:05 PM" or "10:30 AM"
TIME_CELL_RE = re.compile(r"^\d{1,2}:\d{2}\s*(am|pm)$", re.I)

SLOT_STATES = {
    "open": "open",
    "not working": "not_working",
    "blocked": "time_block",
    "time block": "time_block",
    "break": "time_block",
    "lunch": "time_block",
}


def classify_slot_state(client_service: str, status: str) -> str:
    combined = f"{client_service} {status}".lower()
    for kw, state in SLOT_STATES.items():
        if kw in combined:
            return state
    if client_service.strip() and client_service.strip().lower() not in ("", "nan"):
        return "booked"
    return "open"


class StylistDailyScheduleParser(BaseParser):
    report_type = "stylist_daily_schedule"

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

        rows = []
        current_provider_name = None
        current_provider_code = None
        current_date = None
        in_data_section = False
        col_map = {}

        # Carry-forward state: blank rows inherit the previous slot's booking
        cf_state = "open"
        cf_client = None
        cf_service = None

        meta_rows = []

        for idx in range(len(raw_df)):
            row_vals = [str(v).strip() for v in raw_df.iloc[idx]]
            row_text = " ".join(v for v in row_vals if v and v.lower() != "nan").lower()

            # Detect provider header — reset carry-forward for new provider
            m = PROVIDER_HEADER_RE.search(row_text)
            if m or self._is_provider_name_row(row_vals):
                name_val = m.group(1).strip() if m else row_vals[0]
                sf = SCHEDULE_FOR_RE.match(row_text)
                if sf:
                    current_provider_name = sf.group(1).strip().title()
                    from ..normalization.normalizer import normalize_date
                    current_date = normalize_date(sf.group(2).strip())
                else:
                    parts = name_val.rsplit("(", 1)
                    current_provider_name = parts[0].strip()
                    current_provider_code = parts[1].rstrip(")").strip() if len(parts) > 1 else None
                in_data_section = False
                cf_state = "open"
                cf_client = None
                cf_service = None
                continue

            # Detect column header row for this section
            if "time" in row_text and ("client" in row_text or "service" in row_text):
                col_map = {normalize_headers(row_vals)[i]: i for i in range(len(row_vals))}
                in_data_section = True
                cf_state = "open"
                cf_client = None
                cf_service = None
                continue

            # Detect date line
            date_match = re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", row_text)
            if date_match and not in_data_section:
                from ..normalization.normalizer import normalize_date
                current_date = normalize_date(date_match.group())
                meta_rows.append(row_text)
                continue

            if not in_data_section or not col_map:
                if not current_date:
                    import re as re2
                    dm = re2.search(r"\d{1,2}/\d{1,2}/\d{2,4}", row_text)
                    if dm:
                        from ..normalization.normalizer import normalize_date
                        current_date = normalize_date(dm.group())
                continue

            # Data row
            slot_time = row_vals[col_map.get("slot_time", col_map.get("time", 0))] if col_map else row_vals[0]
            if not slot_time or slot_time.lower() in ("nan", ""):
                continue

            cs_idx = col_map.get("client_service_combined", col_map.get("client_name", None))
            client_service = row_vals[cs_idx] if cs_idx is not None and cs_idx < len(row_vals) else ""

            st_idx = col_map.get("status_raw", None)
            status_raw = row_vals[st_idx] if st_idx is not None and st_idx < len(row_vals) else ""

            cs_clean = client_service.strip()
            is_blank = not cs_clean or cs_clean.lower() == "nan"

            if is_blank:
                # Blank row: carry forward the previous slot's state (appointment continuation)
                slot_state = cf_state
                client_part = cf_client
                service_part = cf_service
                is_appt_start = 0
            else:
                # Explicit content: determine new state
                slot_state = classify_slot_state(client_service, status_raw)
                if slot_state == "booked":
                    client_part, service_part = _split_client_service(client_service)
                    cf_client = client_part
                    cf_service = service_part
                    is_appt_start = 1  # new appointment begins here
                else:
                    client_part = None
                    service_part = None
                    cf_client = None
                    cf_service = None
                    is_appt_start = 0
                cf_state = slot_state

            rows.append({
                "_record_type": "provider_schedule_slot",
                "provider_name": current_provider_name,
                "provider_code": current_provider_code,
                "slot_date": current_date,
                "slot_time": slot_time,
                "slot_state": slot_state,
                "is_appt_start": is_appt_start,
                "client_name_raw": client_part if slot_state == "booked" else None,
                "service_description": service_part,
                "status_raw": status_raw or None,
                "notes_raw": None,
                "source_report": self.report_type,
            })

        from ..parsers.header_detector import extract_preamble_metadata
        metadata = extract_preamble_metadata(raw_df, max_rows=5)

        return self._make_result(self.report_type, metadata, file_hash, rows, len(rows))

    def _is_provider_name_row(self, row_vals: list) -> bool:
        non_empty = [v for v in row_vals if v and v.lower() != "nan"]
        if len(non_empty) == 1:
            v = non_empty[0].lower().strip()
            if TIME_CELL_RE.match(v):
                return False
            if not any(kw in v for kw in ("time", "client", "service", "status", "open", "/")):
                return True
        return False


def _split_client_service(combined: str):
    """Split 'Client Name - Service Description' into (client, service)."""
    if not combined or combined.lower() in ("", "nan", "open"):
        return None, None
    # Real format: "Eliza Duran - Girls Cut (Wash, Cut, Blowdry)"
    parts = combined.split(" - ", 1)
    if len(parts) == 2:
        return parts[0].strip() or None, parts[1].strip() or None
    # Fallback: newline-separated
    parts = combined.split("\n", 1)
    if len(parts) == 2:
        return parts[0].strip() or None, parts[1].strip() or None
    return combined.strip() or None, None
