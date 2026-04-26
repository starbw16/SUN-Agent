"""
Parser for Appointment_Audits report.
Shape: preamble + single header + event log rows.
Produces: appointment_event records.
"""
import re
import pandas as pd

from .base_parser import BaseParser
from ..normalization.normalizer import normalize_date, normalize_name, parse_time_range

# Classify event type from action/comments text
ACTION_MAP = {
    "booked": "booked",
    "webappt": "booked_online",
    "online": "booked_online",
    "google": "booked_google",
    "cancel": "cancelled",
    "no show": "no_show",
    "no-show": "no_show",
    "moved": "modified",
    "drag": "modified",
    "drop": "modified",
    "rescheduled": "modified",
    "deleted": "deleted",
    "checked in": "checked_in",
    "completed": "completed",
}


def classify_event_type(action: str, comments: str) -> str:
    combined = f"{action} {comments}".lower()
    for kw, event_type in ACTION_MAP.items():
        if kw in combined:
            return event_type
    return "unknown"


class AppointmentAuditsParser(BaseParser):
    report_type = "appointment_audits"

    def _extract_rows(self, df: pd.DataFrame, metadata: dict) -> list:
        rows = []
        for _, row in df.iterrows():
            client_name_raw = str(row.get("client_name", "")).strip()
            if not client_name_raw or client_name_raw.lower() in ("nan", ""):
                continue

            app_date = normalize_date(row.get("app_date"))
            appt_time_raw = str(row.get("appointment_time", "")).strip()
            start_time, end_time = parse_time_range(appt_time_raw)
            action = str(row.get("action", "")).strip()
            comments = str(row.get("comments", "")).strip()
            remarks = str(row.get("appt_remarks", "")).strip()

            rows.append({
                "_record_type": "appointment_event",
                "app_date": app_date,
                "who_booked": str(row.get("who_booked", "")).strip() or None,
                "client_name_raw": client_name_raw,
                "client_name_normalized": normalize_name(client_name_raw),
                "appointment_time_raw": appt_time_raw,
                "start_time": start_time,
                "end_time": end_time,
                "provider": str(row.get("provider", "")).strip() or None,
                "service_description": str(row.get("service_description", "")).strip() or None,
                "action_raw": action or None,
                "comments": comments or None,
                "appt_remarks": remarks or None,
                "event_type": classify_event_type(action, comments),
                "source_report": self.report_type,
            })
        return rows
