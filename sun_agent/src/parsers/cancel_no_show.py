"""
Parser for Cancel_No_Show report.
Shape: preamble + single header + data table.
Produces: client_risk_snapshot records.
"""
import pandas as pd

from .base_parser import BaseParser
from ..normalization.normalizer import normalize_name, normalize_phone, coalesce_phone


class CancelNoShowParser(BaseParser):
    report_type = "cancel_no_show"

    def _extract_rows(self, df: pd.DataFrame, metadata: dict) -> list:
        rows = []
        for _, row in df.iterrows():
            client_name_raw = str(row.get("client_name", "")).strip()
            if not client_name_raw or client_name_raw.lower() in ("nan", ""):
                continue
            if client_name_raw.lower().startswith("total"):
                continue

            raw_count = row.get("cancel_no_show_count", "")
            try:
                count = int(float(str(raw_count).replace(",", "").strip()))
            except (ValueError, TypeError):
                count = None

            rows.append({
                "_record_type": "client_risk",
                "client_name_raw": client_name_raw,
                "client_name_normalized": normalize_name(client_name_raw),
                "cancel_no_show_count": count,
                "value_raw": str(row.get("value", "")).strip() or None,
                "service_sales_raw": str(row.get("service_sales", "")).strip() or None,
                "retail_sales_raw": str(row.get("retail_sales", "")).strip() or None,
                "total_sales_raw": str(row.get("total_sales", "")).strip() or None,
                "source_report": self.report_type,
            })
        return rows
