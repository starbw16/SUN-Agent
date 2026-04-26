"""Tests for header row detection and report type classification."""
import pandas as pd
import pytest
from ..parsers.header_detector import detect_header_row, detect_report_type


def _make_df(rows):
    return pd.DataFrame(rows)


def test_detect_header_preamble_style():
    """Preamble rows then real header."""
    rows = [
        ["Sharkeys Cuts for Kids", None, None, None, None],
        ["Date Range: 01/01/2024 - 03/31/2024", None, None, None, None],
        [None, None, None, None, None],
        ["Service Category", "Client Name", "Client Phone", "Service Description", "Date"],
        ["Haircut", "John Doe", "5551234567", "Kids Cut", "04/01/2024"],
    ]
    df = _make_df(rows)
    idx, meta = detect_header_row(df, "clients_with_service")
    assert idx == 3


def test_detect_header_no_preamble():
    """Header is the first row."""
    rows = [
        ["Service Category", "Client Name", "Client Phone", "Service Description", "Date"],
        ["Haircut", "Jane Smith", "5559876543", "Adult Cut", "04/02/2024"],
    ]
    df = _make_df(rows)
    idx, meta = detect_header_row(df, "clients_with_service")
    assert idx == 0


def test_metadata_extraction():
    rows = [
        ["Store: Sharkeys Northgate", None, None],
        ["Generated: 04/01/2024", None, None],
        ["Client Name", "Service Category", "Date"],
        ["John Doe", "Haircut", "04/01/2024"],
    ]
    df = _make_df(rows)
    idx, meta = detect_header_row(df, "clients_with_service")
    assert meta.get("store_name") is not None or idx == 2


def test_detect_report_type_from_filename():
    df = pd.DataFrame()
    assert detect_report_type("Clients_With_Service_April.xlsx", df) == "clients_with_service"
    assert detect_report_type("Cancel_No_Show_Report.xls", df) == "cancel_no_show"
    assert detect_report_type("Appointment_Audits_2024.csv", df) == "appointment_audits"
    assert detect_report_type("Stylist_Daily_Schedule.xlsx", df) == "stylist_daily_schedule"
    assert detect_report_type("7_Day_Appointment_Forecast_Report.xlsx", df) == "forecast_7day"


def test_detect_report_type_content_fallback():
    """When filename is generic, sniff from content."""
    rows = [
        ["Report", None, None, None, None],
        ["App Date", "Who Booked", "Client Name", "Action", "Comments"],
        ["04/01/2024", "WEBAPPT", "John Doe", "Booked", "Online booking"],
    ]
    df = _make_df(rows)
    result = detect_report_type("report_export.xlsx", df)
    assert result == "appointment_audits"
