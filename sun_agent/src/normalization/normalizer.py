"""
Header normalization, phone normalization, date normalization, and client key generation.
"""
import re
import hashlib
import unicodedata
from datetime import datetime, timezone
from typing import Optional

# Known misspellings and variants mapped to canonical internal names
HEADER_MAP = {
    "service category": "service_category",
    "client name": "client_name",
    "client phone": "client_phone",
    "service description": "service_description",
    "date": "visit_date",
    "appointment time": "appointment_time",
    "stylist code": "stylist_code",
    "home phone #": "home_phone",
    "home phone": "home_phone",
    "bussiness phone #": "business_phone",
    "business phone #": "business_phone",
    "business phone": "business_phone",
    "cell phone #": "cell_phone",
    "cell phone": "cell_phone",
    "pref. cont.": "preferred_contact",
    "pref cont": "preferred_contact",
    "preferred contact": "preferred_contact",
    "status": "status_raw",
    "client name / service": "client_service_combined",
    "client + service": "client_service_combined",
    "time": "slot_time",
    "prev prov": "prev_provider",
    "prev time": "prev_time",
    "prev serv": "prev_service",
    "next prov": "next_provider",
    "next time": "next_time",
    "next serv": "next_service",
    "app date": "app_date",
    "who booked": "who_booked",
    "appointment time": "appointment_time",
    "provider": "provider",
    "service": "service_description",
    "action": "action",
    "comments": "comments",
    "appt remarks": "appt_remarks",
    "total cancel/no shows": "cancel_no_show_count",
    "total cancel/ no shows": "cancel_no_show_count",
    "value": "value",
    "service sales": "service_sales",
    "retail sales": "retail_sales",
    "total sales": "total_sales",
    "employee code": "employee_code",
}


def normalize_header(raw: str) -> str:
    """Map a raw header string to a canonical internal field name."""
    cleaned = raw.strip().lower()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return HEADER_MAP.get(cleaned, cleaned.replace(" ", "_").replace("/", "_").replace("#", "").strip("_"))


def normalize_headers(raw_headers: list) -> list:
    return [
        normalize_header(str(h))
        if str(h).strip() and str(h).strip().lower() not in ("none", "nan")
        else f"_blank_{i}"
        for i, h in enumerate(raw_headers)
    ]


def is_blank_column(name: str) -> bool:
    return name.startswith("_blank_") or not name.strip("_")


def normalize_phone(raw: str) -> Optional[str]:
    """Strip to digits only; return None if fewer than 7 digits."""
    if not raw or str(raw).strip() in ("", "nan", "None"):
        return None
    digits = re.sub(r"\D", "", str(raw))
    if len(digits) < 7:
        return None
    if len(digits) == 11 and digits[0] == "1":
        digits = digits[1:]
    return digits[:10] if len(digits) >= 10 else digits


def normalize_name(raw: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    if not raw:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(raw))
    ascii_str = nfkd.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_str.lower().strip())


def normalize_date(raw) -> Optional[str]:
    """Return ISO date string YYYY-MM-DD or None."""
    if raw is None or str(raw).strip() in ("", "nan", "None", "NaT"):
        return None
    raw_str = str(raw).strip()
    # Strip trailing parenthetical like "(today)"
    raw_str = re.sub(r"\s*\(.*?\)\s*$", "", raw_str).strip()
    # Formats that use only the first token (date may have trailing time)
    first_token_fmts = ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y")
    # Formats that require the full string (month-name dates)
    full_str_fmts = ("%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y")
    for fmt in first_token_fmts:
        try:
            return datetime.strptime(raw_str.split()[0], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    for fmt in full_str_fmts:
        try:
            return datetime.strptime(raw_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return raw_str


def make_client_key(store_id: str, client_name: str, phone: Optional[str]) -> str:
    """Deterministic key: sha256(store_id|normalized_name|normalized_phone)."""
    name_norm = normalize_name(client_name)
    phone_norm = phone or ""
    raw = f"{store_id.lower()}|{name_norm}|{phone_norm}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def make_household_key(store_id: str, phone: Optional[str]) -> Optional[str]:
    """Key grouping all clients on the same phone number within a store."""
    if not phone:
        return None
    raw = f"{store_id.lower()}|{phone}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def coalesce_phone(*args) -> Optional[str]:
    """Return first non-None normalized phone from a list of raw values."""
    for raw in args:
        result = normalize_phone(str(raw) if raw is not None else "")
        if result:
            return result
    return None


def parse_time_range(raw: str):
    """
    Split '9:00 AM - 9:15 AM' into ('09:00', '09:15').
    Returns (start_time, end_time) as HH:MM strings or (None, None).
    """
    if not raw or str(raw).strip() in ("", "nan"):
        return None, None
    raw = str(raw).strip()
    parts = re.split(r"\s*-\s*", raw)
    if len(parts) == 2:
        return _parse_time(parts[0]), _parse_time(parts[1])
    return _parse_time(raw), None


def _parse_time(raw: str) -> Optional[str]:
    raw = raw.strip()
    for fmt in ("%I:%M %p", "%H:%M", "%I:%M%p", "%I %p"):
        try:
            return datetime.strptime(raw, fmt).strftime("%H:%M")
        except ValueError:
            continue
    return raw if raw else None
