"""Parser-level tests using synthetic in-memory DataFrames."""
import io
import tempfile
import os
from pathlib import Path

import pandas as pd
import pytest

from ..parsers.clients_with_service import ClientsWithServiceParser
from ..parsers.appt_conf_list import ApptConfListParser
from ..parsers.cancel_no_show import CancelNoShowParser
from ..parsers.appointment_audits import AppointmentAuditsParser
from ..parsers.stylist_daily_schedule import StylistDailyScheduleParser
from ..parsers.forecast_7day import Forecast7DayParser
from ..normalization.normalizer import normalize_headers, is_blank_column


def _write_csv(rows, tmpdir, filename) -> Path:
    path = Path(tmpdir) / filename
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, header=False)
    return path


def test_clients_with_service_parser(tmp_path):
    rows = [
        ["Sharkeys Test Store", None, None, None, None],
        ["Date Range: 01/01/2024 - 03/31/2024", None, None, None, None],
        ["Service Category", "Client Name", "Client Phone", "Service Description", "Date"],
        ["Haircut", "John Doe", "5551234567", "Kids Cut", "04/01/2024"],
        ["Haircut", "Jane Smith", "5559876543", "Trim", "04/02/2024"],
        [None, None, None, None, None],
    ]
    path = _write_csv(rows, tmp_path, "Clients_With_Service.csv")
    parser = ClientsWithServiceParser(path)
    result = parser.parse()
    assert result.ok
    assert len(result.rows) == 2
    assert result.rows[0]["client_name_raw"] == "John Doe"
    assert result.rows[0]["visit_date"] == "2024-04-01"
    assert result.rows[1]["primary_phone"] == "5559876543"


def test_clients_with_service_skips_blank_rows(tmp_path):
    rows = [
        ["Service Category", "Client Name", "Client Phone", "Service Description", "Date"],
        ["Haircut", "John Doe", "5551234567", "Kids Cut", "04/01/2024"],
        [None, None, None, None, None],
        ["Haircut", "", "", "Trim", "04/02/2024"],
    ]
    path = _write_csv(rows, tmp_path, "Clients_With_Service.csv")
    parser = ClientsWithServiceParser(path)
    result = parser.parse()
    assert result.ok
    data_rows = [r for r in result.rows if r.get("client_name_raw")]
    assert len(data_rows) == 1


def test_appt_conf_list_time_split(tmp_path):
    rows = [
        ["Store: Test", None, None, None, None, None, None],
        ["Appointment Time", "Stylist Code", "Client Name", "Service Description",
         "Home Phone #", "Bussiness Phone #", "Cell Phone #", "Pref. Cont.", "Status"],
        ["9:00 AM - 9:15 AM", "JESS", "John Doe", "Kids Cut",
         "5551234567", "", "5557654321", "Cell", "Booked"],
    ]
    path = _write_csv(rows, tmp_path, "Appt_Conf_List_Time.csv")
    parser = ApptConfListParser(path)
    result = parser.parse()
    assert result.ok
    assert len(result.rows) >= 1
    r = result.rows[0]
    assert r["start_time"] == "09:00"
    assert r["end_time"] == "09:15"
    assert r["stylist_code"] == "JESS"


def test_cancel_no_show_parser(tmp_path):
    rows = [
        ["Store Header", None, None, None, None, None],
        ["Client Name", "Total Cancel/No Shows", "Value", "Service Sales", "Retail Sales", "Total Sales"],
        ["Problem Client", "4", "50.00", "45.00", "5.00", "50.00"],
        ["Good Client", "0", "100.00", "90.00", "10.00", "100.00"],
    ]
    path = _write_csv(rows, tmp_path, "Cancel_No_Show.csv")
    parser = CancelNoShowParser(path)
    result = parser.parse()
    assert result.ok
    assert len(result.rows) == 2
    assert result.rows[0]["cancel_no_show_count"] == 4
    assert result.rows[1]["cancel_no_show_count"] == 0


def test_appointment_audits_event_classification(tmp_path):
    rows = [
        ["Header Row", None, None, None, None, None, None, None],
        ["App Date", "Who Booked", "Client Name", "Appointment Time",
         "Provider", "Service", "Action", "Comments", "Appt Remarks"],
        ["04/01/2024", "WEBAPPT", "John Doe", "9:00 AM - 9:15 AM",
         "JESS", "Kids Cut", "Booked", "WEBAPPT online booking", ""],
        ["04/01/2024", "MGR", "John Doe", "9:00 AM - 9:15 AM",
         "JESS", "Kids Cut", "Cancel", "Client called to cancel", ""],
    ]
    path = _write_csv(rows, tmp_path, "Appointment_Audits.csv")
    parser = AppointmentAuditsParser(path)
    result = parser.parse()
    assert result.ok
    assert len(result.rows) >= 2
    event_types = {r["event_type"] for r in result.rows}
    assert "booked_online" in event_types or "booked" in event_types
    assert "cancelled" in event_types


def test_blank_column_removal(tmp_path):
    rows = [
        ["Client Name", "", "Date", None, "Service"],
        ["John Doe", "", "04/01/2024", None, "Haircut"],
    ]
    path = _write_csv(rows, tmp_path, "Clients_With_Service.csv")
    parser = ClientsWithServiceParser(path)
    result = parser.parse()
    assert result.ok


def test_header_normalization_misspelling():
    raw = ["Bussiness Phone #", "Client Name", "Pref. Cont.", "Total Cancel/No Shows"]
    normalized = normalize_headers(raw)
    assert "business_phone" in normalized
    assert "client_name" in normalized
    assert "preferred_contact" in normalized
    assert "cancel_no_show_count" in normalized


def test_forecast_7day_parser(tmp_path):
    rows = [
        ["Store Header", None, None, None, None, None],
        [None, "04/01/2024", "04/01/2024", "04/02/2024", "04/02/2024", None],
        [None, "Booked by Receptionist", "Online Booked", "Booked by Receptionist", "Online Booked", None],
        ["Total", "5", "3", "7", "2", None],
    ]
    path = _write_csv(rows, tmp_path, "7_Day_Appointment_Forecast_Report.csv")
    parser = Forecast7DayParser(path)
    result = parser.parse()
    assert result.ok
    assert len(result.rows) >= 1
    channels = {r["booking_channel"] for r in result.rows}
    assert any("receptionist" in c or "online" in c for c in channels)
