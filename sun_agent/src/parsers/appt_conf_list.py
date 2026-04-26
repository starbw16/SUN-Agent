"""
Parser for Appt_Conf_List(Time) report.
Shape: preamble + single header + data table with blank spacer columns.
Produces: appointment records.
"""
import pandas as pd

from .base_parser import BaseParser
from ..normalization.normalizer import (
    normalize_date, normalize_name, coalesce_phone, parse_time_range
)


class ApptConfListParser(BaseParser):
    report_type = "appt_conf_list"

    def _extract_rows(self, df: pd.DataFrame, metadata: dict) -> list:
        # "Report date:" preamble value lands in report_title due to pattern match on "report"
        appt_date = normalize_date(
            metadata.get("report_title") or metadata.get("report_date") or metadata.get("report_period")
        )

        rows = []
        for _, row in df.iterrows():
            client_name_raw = str(row.get("client_name", "")).strip()
            if not client_name_raw or client_name_raw.lower() in ("nan", ""):
                continue

            appt_time_raw = str(row.get("appointment_time", "")).strip()
            start_time, end_time = parse_time_range(appt_time_raw)

            phone = coalesce_phone(
                row.get("cell_phone"),
                row.get("home_phone"),
                row.get("business_phone"),
            )

            rows.append({
                "_record_type": "appointment",
                "client_name_raw": client_name_raw,
                "client_name_normalized": normalize_name(client_name_raw),
                "primary_phone": phone,
                "stylist_code": str(row.get("stylist_code", "")).strip() or None,
                "appointment_date": appt_date,
                "appointment_time_raw": appt_time_raw,
                "start_time": start_time,
                "end_time": end_time,
                "service_description": str(row.get("service_description", "")).strip().lstrip("-").strip() or None,
                "preferred_contact": str(row.get("preferred_contact", "")).strip() or None,
                "status_raw": str(row.get("status_raw", "")).strip() or None,
                "booking_source": None,
                "source_report": self.report_type,
            })
        return rows
