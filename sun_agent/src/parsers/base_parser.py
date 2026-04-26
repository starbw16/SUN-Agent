"""
Base parser: loads Excel/CSV, strips blank columns, detects header, returns ParseResult.
All report-specific parsers inherit from this.
"""
import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from ..parsers.header_detector import detect_header_row, detect_report_type
from ..normalization.normalizer import normalize_headers, is_blank_column

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    report_type: str
    store_name_raw: Optional[str]
    report_start_date: Optional[str]
    report_end_date: Optional[str]
    generated_at: Optional[str]
    file_hash: str
    source_filename: str
    rows: list = field(default_factory=list)
    row_count_raw: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class BaseParser:
    report_type = "unknown"

    def __init__(self, filepath: Path):
        self.filepath = Path(filepath)

    def _file_hash(self) -> str:
        h = hashlib.sha256()
        with open(self.filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def _load_raw(self) -> pd.DataFrame:
        suffix = self.filepath.suffix.lower()

        if suffix == ".csv":
            return pd.read_csv(self.filepath, header=None, dtype=str)

        if suffix in (".xls", ".xlsx"):
            # Standard path
            engine = "xlrd" if suffix == ".xls" else "openpyxl"
            try:
                return pd.read_excel(self.filepath, header=None, dtype=str, engine=engine)
            except Exception:
                pass

            # Apple Numbers / some Salon Ultimate XLS exports have OLE2 sector
            # allocation issues that xlrd rejects. Read directly with corruption flag.
            if suffix == ".xls":
                try:
                    import xlrd
                    wb = xlrd.open_workbook(
                        str(self.filepath), ignore_workbook_corruption=True
                    )
                    ws = wb.sheet_by_index(0)
                    data = [
                        [str(ws.cell_value(r, c)) for c in range(ws.ncols)]
                        for r in range(ws.nrows)
                    ]
                    return pd.DataFrame(data)
                except Exception:
                    pass

            # Salon Ultimate sometimes exports HTML tables with a .xls extension.
            try:
                tables = pd.read_html(self.filepath, header=None, dtype=str)
                if tables:
                    return max(tables, key=len).astype(str)
            except Exception:
                pass

            # Last resort: CSV with tab or comma delimiter
            for sep in ("\t", ","):
                try:
                    return pd.read_csv(self.filepath, header=None, dtype=str, sep=sep)
                except Exception:
                    pass

        raise ValueError(f"Could not parse file: {self.filepath.name}")

    def _strip_blank_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        keep = [i for i, col in enumerate(df.columns) if not is_blank_column(str(col))]
        return df.iloc[:, keep]

    def _make_result(self, report_type, metadata, file_hash, rows, row_count_raw):
        return ParseResult(
            report_type=report_type,
            store_name_raw=metadata.get("store_name"),
            report_start_date=metadata.get("report_period"),
            report_end_date=None,
            generated_at=metadata.get("generated_at"),
            file_hash=file_hash,
            source_filename=self.filepath.name,
            rows=rows,
            row_count_raw=row_count_raw,
        )

    def parse(self) -> ParseResult:
        file_hash = self._file_hash()
        try:
            raw_df = self._load_raw()
            report_type = detect_report_type(self.filepath.name, raw_df)
            header_idx, metadata = detect_header_row(raw_df, report_type)

            # Build dataframe with proper header
            header_row = [str(v) for v in raw_df.iloc[header_idx]]
            data_df = raw_df.iloc[header_idx + 1:].copy()
            data_df.columns = normalize_headers(header_row)

            # Drop blank columns
            data_df = data_df[[c for c in data_df.columns if not is_blank_column(c)]]

            # Drop fully empty rows
            data_df = data_df.dropna(how="all")

            row_count_raw = len(data_df)
            rows = self._extract_rows(data_df, metadata)
            return self._make_result(report_type, metadata, file_hash, rows, row_count_raw)

        except Exception as exc:
            logger.exception("Parse failed: %s", self.filepath)
            return ParseResult(
                report_type=self.report_type,
                store_name_raw=None,
                report_start_date=None,
                report_end_date=None,
                generated_at=None,
                file_hash=file_hash,
                source_filename=self.filepath.name,
                error=str(exc),
            )

    def _extract_rows(self, df: pd.DataFrame, metadata: dict) -> list:
        """Override in subclasses to produce list of dicts."""
        return df.to_dict("records")
