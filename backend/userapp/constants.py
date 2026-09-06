DEFAULT_DEPARTMENTS = {
    "CSC": "Computer Science",
    "STA": "Statistics",
    "MTH": "Mathematics",
    "PHY": "Physics",
    "ICH": "Industrial Chemistry",
    "MCB": "Microbiology",
}

DEPARTMENTS = DEFAULT_DEPARTMENTS

MIN_LEVEL = 100
MAX_LEVEL = 500

LEVEL_CHOICES = [(lvl, f"{lvl} Level") for lvl in range(MIN_LEVEL, MAX_LEVEL + 1, 100)]


def department_name(code):
    return DEPARTMENTS.get(code, code)


def session_start_year(today=None):
    import datetime

    today = today or datetime.date.today()
    return today.year if today.month >= 8 else today.year - 1


def session_label(start_year=None):
    start = start_year or session_start_year()
    return f"{start}/{str(start + 1)[2:]}"
