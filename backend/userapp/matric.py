import datetime
import re

from rest_framework.exceptions import ValidationError

from .constants import MAX_LEVEL, MIN_LEVEL
from .models import Department, SessionConfig

MATRIC_PATTERN = r"^([A-Z]{2,5})/(\d{2})/(\d{2,6})$"
MATRIC_RE = re.compile(MATRIC_PATTERN)


def normalize_matric(value):
    cleaned = re.sub(r"[\s\-]+", "", (value or "")).upper()
    if "/" not in cleaned:
        cleaned = cleaned.replace("-", "/")
    parts = [p for p in cleaned.split("/") if p]
    return "/".join(parts)


def parse_matric(raw_value):
    value = normalize_matric(raw_value)
    match = MATRIC_RE.match(value)
    if not match:
        raise ValidationError(
            {
                "matric_number": (
                    "Invalid matric number format. Expected DEPT/YY/NNN "
                    "(e.g. STA/24/020)."
                )
            }
        )

    dept_code, yy, number = match.groups()
    active_depts = Department.get_active_map()
    if dept_code not in active_depts:
        raise ValidationError(
            {
                "matric_number": (
                    f"Unknown or inactive department code '{dept_code}'. Allowed codes: "
                    + ", ".join(sorted(active_depts.keys()))
                    + "."
                )
            }
        )

    entry_year = 2000 + int(yy)
    session_start = SessionConfig.active_start()
    if entry_year > session_start:
        raise ValidationError(
            {
                "matric_number": (
                    "Matric number admission year is after the active "
                    f"session ({session_start}/{str(session_start + 1)[2:]})."
                )
            }
        )
    if entry_year > datetime.date.today().year:
        raise ValidationError(
            {"matric_number": "Matric number contains a future admission year."}
        )

    # 1st year (entry_year == session_start) is 100L, 2nd year is 200L, etc.
    years_in_school = session_start - entry_year + 1
    level = min(max(years_in_school * 100, MIN_LEVEL), MAX_LEVEL)

    return {
        "normalized": value,
        "department_code": dept_code,
        "department_name": active_depts[dept_code],
        "entry_year": entry_year,
        "number": number,
        "level": level,
    }

