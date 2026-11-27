import datetime
import re

from rest_framework.exceptions import ValidationError

from .constants import MAX_LEVEL, MIN_LEVEL
from .models import Department, SessionConfig

MATRIC_PATTERN = r"^([A-Z]{2,5})/(?:(DE)/)?(\d{2})/(\d{2,6})$"
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
                    "Invalid matric number format. Expected DEPT/YY/NNN or "
                    "DEPT/DE/YY/NNN (e.g. STA/24/020 or STA/DE/25/020)."
                )
            }
        )

    dept_code, direct_entry, yy, number = match.groups()
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

    # The year is the student's actual admission year, including Direct Entry.
    entry_level = 200 if direct_entry else 100
    level = min(max(entry_level + (session_start - entry_year) * 100, MIN_LEVEL), MAX_LEVEL)

    return {
        "normalized": value,
        "department_code": dept_code,
        "department_name": active_depts[dept_code],
        "entry_year": entry_year,
        "entry_level": entry_level,
        "direct_entry": bool(direct_entry),
        "number": number,
        "level": level,
    }

