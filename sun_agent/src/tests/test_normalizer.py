"""Tests for normalizer functions."""
import pytest
from ..normalization.normalizer import (
    normalize_header, normalize_headers, normalize_phone,
    normalize_name, normalize_date, make_client_key,
    make_household_key, coalesce_phone, parse_time_range, is_blank_column,
)


def test_normalize_header_known():
    assert normalize_header("Bussiness Phone #") == "business_phone"
    assert normalize_header("Client Name") == "client_name"
    assert normalize_header("Pref. Cont.") == "preferred_contact"
    assert normalize_header("Total Cancel/No Shows") == "cancel_no_show_count"


def test_normalize_header_unknown():
    result = normalize_header("Some Unknown Field")
    assert result == "some_unknown_field"


def test_blank_column_detection():
    assert is_blank_column("_blank_0")
    assert is_blank_column("_blank_12")
    assert not is_blank_column("client_name")
    assert not is_blank_column("service_category")


def test_normalize_headers_removes_blanks():
    headers = ["Client Name", "", "Date", None, "Service"]
    result = normalize_headers(headers)
    blank_cols = [h for h in result if is_blank_column(h)]
    data_cols = [h for h in result if not is_blank_column(h)]
    assert len(blank_cols) == 2
    assert "client_name" in data_cols
    assert "visit_date" in data_cols


def test_normalize_phone_valid():
    assert normalize_phone("(555) 123-4567") == "5551234567"
    assert normalize_phone("555.123.4567") == "5551234567"
    assert normalize_phone("15551234567") == "5551234567"


def test_normalize_phone_invalid():
    assert normalize_phone("") is None
    assert normalize_phone("123") is None
    assert normalize_phone("nan") is None


def test_normalize_name():
    assert normalize_name("  John  DOE  ") == "john doe"
    assert normalize_name("José García") == "jose garcia"


def test_normalize_date_formats():
    assert normalize_date("04/15/2024") == "2024-04-15"
    assert normalize_date("4/5/24") == "2024-04-05"
    assert normalize_date("2024-04-15") == "2024-04-15"
    assert normalize_date("") is None
    assert normalize_date(None) is None


def test_make_client_key_deterministic():
    k1 = make_client_key("store1", "john doe", "5551234567")
    k2 = make_client_key("store1", "john doe", "5551234567")
    assert k1 == k2
    assert len(k1) == 16


def test_make_client_key_different_stores():
    k1 = make_client_key("store1", "john doe", "5551234567")
    k2 = make_client_key("store2", "john doe", "5551234567")
    assert k1 != k2


def test_household_key():
    hk = make_household_key("store1", "5551234567")
    assert hk is not None
    assert len(hk) == 16
    assert make_household_key("store1", None) is None


def test_coalesce_phone():
    assert coalesce_phone(None, "5551234567", "5559999999") == "5551234567"
    assert coalesce_phone(None, None, None) is None
    assert coalesce_phone("(555) 123-4567") == "5551234567"


def test_parse_time_range():
    start, end = parse_time_range("9:00 AM - 9:15 AM")
    assert start == "09:00"
    assert end == "09:15"


def test_parse_time_range_empty():
    start, end = parse_time_range("")
    assert start is None
    assert end is None
