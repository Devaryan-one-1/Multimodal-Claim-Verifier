"""Read explicit reporting-period wording without inferring fiscal calendars."""
from __future__ import annotations

from datetime import date
import re

MONTH = r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
MONTHS = {name: index for index, name in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
DATE = re.compile(rf"\b({MONTH})\.?\s+(\d{{1,2}}),?\s+((?:19|20)\d{{2}})\b|\b((?:19|20)\d{{2}})-(\d{{2}})-(\d{{2}})\b", re.I)


def explicit_dates(text):
    result = []
    for match in DATE.finditer(str(text or "")):
        month, day, year, iso_year, iso_month, iso_day = match.groups()
        try:
            value = date(int(year), MONTHS[month[:3].lower()], int(day)) if year else date(int(iso_year), int(iso_month), int(iso_day))
        except ValueError:
            continue
        result.append((value.isoformat(), match))
    return result


def explicit_quarters(text):
    ordinals={"first":1,"second":2,"third":3,"fourth":4}
    return {int(number) if number else ordinals[word.casefold()] for number,word in
            re.findall(r"\bq([1-4])\b|\b(first|second|third|fourth)\s+(?:fiscal\s+)?quarter\b",str(text or ""),re.I)}


def explicit_period(text):
    """Return a period and its original wording only when the wording is explicit."""
    durations = []
    for pattern, period in ((r"\b(?:three|3)[ -]months?\b", "quarter"),
                            (r"\b(?:six|6)[ -]months?\b", "six_months"),
                            (r"\b(?:nine|9)[ -]months?\b", "nine_months"),
                            (r"\b(?:twelve|12)[ -]months?\b", "annual"),
                            (r"\b(?:quarter(?:ly)?|q[1-4])\b", "quarter"),
                            (r"\b(?:years?\s+ended|fiscal\s+year|annual|fy\s*(?:19|20)\d{2})\b", "annual")):
        if match := re.search(pattern, text, re.I):
            durations.append((period, match.group()))
    if len({period for period, _ in durations}) > 1:
        raise ValueError("One atomic claim contains conflicting reporting durations; split its separate period/value assertions.")
    if durations:
        return durations[0]
    for _, match in explicit_dates(text):
        if prefix := re.search(r"\b(?:as\s+of|as\s+at|at|on)\s*$", text[:match.start()], re.I):
            return "point_in_time", text[prefix.start():match.end()]
    return None
