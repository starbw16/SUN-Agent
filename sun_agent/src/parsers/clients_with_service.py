"""
Parser for Clients_With_Service report.
Shape: preamble + single header row + data table.
Produces: client upsert records + visit fact records.
"""
from pathlib import Path

import pandas as pd

from .base_parser import BaseParser, ParseResult
from ..normalization.normalizer import (
    normalize_date, normalize_phone, normalize_name, make_client_key, make_household_key, coalesce_phone
)


class ClientsWithServiceParser(BaseParser):
    report_type = "clients_with_service"

    def _extract_rows(self, df: pd.DataFrame, metadata: dict) -> list:
        rows = []
        for _, row in df.iterrows():
            client_name_raw = str(row.get("client_name", "")).strip()
            if not client_name_raw or client_name_raw.lower() in ("nan", ""):
                continue

            phone_raw = str(row.get("client_phone", "")).strip()
            phone = normalize_phone(phone_raw)
            visit_date = normalize_date(row.get("visit_date"))

            rows.append({
                "_record_type": "client_visit",
                "client_name_raw": client_name_raw,
                "client_name_normalized": normalize_name(client_name_raw),
                "primary_phone": phone,
                "household_key_seed": phone,
                "visit_date": visit_date,
                "service_category": str(row.get("service_category", "")).strip() or None,
                "service_description": str(row.get("service_description", "")).strip() or None,
                "source_report": self.report_type,
            })
        return rows
