"""
Routes a file to the correct parser class based on report type detection.
"""
from pathlib import Path

from .header_detector import detect_report_type
from .base_parser import BaseParser
from .clients_with_service import ClientsWithServiceParser
from .appt_conf_list import ApptConfListParser
from .stylist_daily_schedule import StylistDailyScheduleParser
from .cancel_no_show import CancelNoShowParser
from .appointment_audits import AppointmentAuditsParser
from .forecast_7day import Forecast7DayParser
from .weekly_timesheet import WeeklyTimesheetParser

import pandas as pd

PARSER_REGISTRY = {
    "clients_with_service": ClientsWithServiceParser,
    "appt_conf_list": ApptConfListParser,
    "stylist_daily_schedule": StylistDailyScheduleParser,
    "cancel_no_show": CancelNoShowParser,
    "appointment_audits": AppointmentAuditsParser,
    "forecast_7day": Forecast7DayParser,
    "weekly_timesheet": WeeklyTimesheetParser,
}

PHASE_1_TYPES = {
    "clients_with_service",
    "appt_conf_list",
    "stylist_daily_schedule",
    "cancel_no_show",
    "appointment_audits",
}

SUPPORTED_TYPES = set(PARSER_REGISTRY.keys())


def get_parser(filepath: Path) -> BaseParser:
    """Return the appropriate parser instance for the given file."""
    path = Path(filepath)
    suffix = path.suffix.lower()
    if suffix in (".xls", ".xlsx"):
        try:
            df = pd.read_excel(path, header=None, dtype=str, nrows=15)
        except Exception:
            df = pd.DataFrame()
    elif suffix == ".csv":
        try:
            df = pd.read_csv(path, header=None, dtype=str, nrows=15)
        except Exception:
            df = pd.DataFrame()
    else:
        return BaseParser(filepath)

    report_type = detect_report_type(path.name, df)
    parser_class = PARSER_REGISTRY.get(report_type, BaseParser)
    return parser_class(filepath)


def is_phase1(report_type: str) -> bool:
    return report_type in PHASE_1_TYPES
