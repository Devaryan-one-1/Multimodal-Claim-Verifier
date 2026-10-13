"""Deterministic financial arithmetic. Round only when presenting a result."""
from __future__ import annotations

import math

from verification.number_parser import parse_number, parse_percent


def parse_value(value):
    parsed, reason = parse_number(value)
    return (parsed["value"], reason) if parsed else (None, reason)


def parse_percent_value(value):
    parsed, reason = parse_percent(value)
    return (parsed["value"], reason) if parsed else (None, reason)


def _result(value, formula, inputs, note=None):
    if value is not None and not math.isfinite(value):
        value, note = None, "The calculation is not finite."
    return {"value": value, "formula": formula, "inputs": inputs, "note": note}


def _pair(first, second):
    a, ar = parse_value(first)
    b, br = parse_value(second)
    return a, b, "; ".join(str(x) for x in (ar, br) if x) or None


def percentage_growth(old, new):
    a, b, note = _pair(old, new)
    inputs, formula = {"old": a, "new": b}, "(new - old)/old*100"
    if a is None or b is None:
        return _result(None, formula, inputs, note or "An input is unavailable.")
    if a == 0:
        return _result(None, formula, inputs, "Relative percentage growth is undefined for a zero baseline.")
    if a < 0:
        return _result(None, formula, inputs, "A negative baseline requires an explicit comparison convention.")
    return _result((b - a) / a * 100, formula, inputs, note)


def absolute_change(old, new):
    a, b, note = _pair(old, new)
    return _result(None if a is None or b is None else b - a, "new - old", {"old": a, "new": b}, note)


def margin(profit, revenue):
    a, b, note = _pair(profit, revenue)
    if b == 0:
        note = "Margin is undefined because revenue is zero."
    return _result(None if a is None or b is None or b == 0 else a / b * 100,
                   "profit / revenue * 100", {"profit": a, "revenue": b}, note)


def cagr(beginning, ending, years):
    a, b, note = _pair(beginning, ending)
    span, reason = parse_value(years)
    inputs = {"beginning": a, "ending": b, "years": span}
    formula = "(ending/beginning)^(1/years)-1"
    if a is None or b is None or span is None:
        return _result(None, formula, inputs, reason or note or "An input is unavailable.")
    if a <= 0 or b < 0 or span <= 0:
        return _result(None, formula, inputs, "CAGR needs a positive beginning and year span, and a nonnegative ending.")
    return _result(((b / a) ** (1 / span) - 1) * 100, formula, inputs, note)


def percentage_point_change(old_margin, new_margin):
    a, b, note = _pair(old_margin, new_margin)
    return _result(None if a is None or b is None else b - a, "new_margin - old_margin",
                   {"old_margin": a, "new_margin": b}, note)


def analyze_change(old, new, claim_wording=None):
    a, ar = parse_percent_value(old)
    b, br = parse_percent_value(new)
    wording = str(claim_wording or "").casefold()
    intended = "pp" if any(x in wording for x in ("percentage point", "points", "ppt", " pp")) else (
        "relative" if any(x in wording for x in ("%", "percent", "growth", "grew", "relative")) else None)
    if a is None or b is None:
        return {"pp_change": None, "relative_change": None, "likely_intended": intended,
                "inputs": {"old": old, "new": new}, "note": ar or br or "Percentage inputs are unavailable."}
    relative = percentage_growth(a, b)
    return {"pp_change": b - a, "relative_change": relative["value"], "likely_intended": intended,
            "inputs": {"old": a, "new": b, "unit": "percent"},
            "raw_inputs": {"old": old, "new": new},
            "note": relative.get("note") or "Percentage-point change and relative percentage growth are different quantities."}

# from number_parser import parse_number, parse_percent

# SCALE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}
# _PP_WORDS = ("percentage point", "percentage points", "pp", "ppt", "points", "point")
# _REL_WORDS = ("percent", "%", "relative", "grew", "rose", "increase", "increased", "growth")

# def parse_value(v):
#     result, note = parse_number(v)
#     if result is None:
#         return None, note
#     # calculations are currency-agnostic: suppress the "assumed USD" note here
#     if note and "assumed USD" in note:
#         note = None
#     return result["value"], note
#     # result, note = parse_number(v)
#     # if result is None:
#     #     return None, note
#     # return result["value"], note

#     # if isinstance(v, (int, float)):
#     #     return _clean_zero(float(v)), None
#     # if not isinstance(v, str):
#     #     return None, f'unparsable value type: {type(v).__name__}'

#     # s = v.strip()

#     # if "%" in s:
#     #     return None, "percent"

#     # note = None

#     # negative = False
#     # if s.startswith("(") and s.endswith(")"):
#     #     negative = True
#     #     s = s[1: -1].strip()
#     #     note = "parenthseis interpreted as negative value"
#     # s = s.replace("$", "").strip()

#     # if "," in s:
#     #     parts = s.split(",")
#     #     looks_like_thousands = (
#     #         len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:])
#     #     )
#     #     if looks_like_thousands:
#     #         s = "".join(parts)
#     #     else:
#     #         return None, f'could not parse value {v!r} malformed commas'

#     # scale = 1
#     # if s and s[-1].lower() in SCALE:
#     #     scale = SCALE[s[-1].lower()]
#     #     s = s[:-1].strip()

#     # if s.startswith("-"):
#     #     negative = True
#     #     s = s[1:].strip()

#     # try:
#     #     num = float(s) * scale
#     # except ValueError:
#     #     return None, f'could not parse value {v!r} may need llm normalization'

#     # if negative:
#     #     num = -num
#     # return _clean_zero(num), note

# def parse_percent_value(v):
#     if isinstance(v, (int, float)):
#         return _clean_zero(float(v)), "percent convention unresolved (numeric input)"
#     if isinstance(v, str) and "%" in v:
#         s = v.replace("%", "").strip()
#         try:
#             return _clean_zero(float(s)), "percent convention unresolved; '%' stripped to face value"
#         except ValueError:
#             return None, f"could not parse percent: {v!r}"
#     return None, "not a recognized percent value"


# def analyze_change(old, new, claim_wording=None):
#     """
#     For a change between two percentages (e.g. margin 20% -> 30%), compute BOTH
#     interpretations, because both are valid answers to different questions:
#       - pp_change:       new - old            (20 -> 30 = 10 percentage points)
#       - relative_change: (new-old)/old * 100  (20 -> 30 = 50%)

#     If claim_wording is given, 'likely_intended' is picked from the wording
#     ('points'/'percentage points' -> pp; 'percent'/'%'/'grew' -> relative).

#     Returns an auditable dict with both results, the pick, and notes.
#     """
#     o_typed, o_note = parse_percent(old)
#     n_typed, n_note = parse_percent(new)
#     notes = [x for x in (o_note, n_note) if x]

#     if o_typed is None or n_typed is None:
#         return {
#             "pp_change": None,
#             "relative_change": None,
#             "likely_intended": None,
#             "inputs": {"old": old, "new": new},
#             "note": _join("; ".join(notes) if notes else None,
#                           "undefined: could not parse a percentage input"),
#         }

#     o = o_typed["value"]
#     n = n_typed["value"]

#     # both interpretations, reusing the existing calculations
#     pp = percentage_point_change(o, n)          # new - old  (percentage points)
#     rel = percentage_growth(o, n)               # (new-old)/old*100  (relative %)

#     intended = _pick_interpretation(claim_wording)

#     return {
#         "pp_change": pp["value"],               # e.g. 10.0 (points)
#         "relative_change": rel["value"],        # e.g. 50.0 (%)
#         "likely_intended": intended,            # 'pp' | 'relative' | None
#         "inputs": {"old": o, "new": n, "unit": "percent"},
#         "note": _join(
#             "; ".join(notes) if notes else None,
#             "pp_change is in percentage points; relative_change is percent growth"
#             + (f"; wording suggests '{intended}'" if intended else "")
#         ),
#     }


# def _pick_interpretation(claim_wording):
#     """Decide 'pp' vs 'relative' from the claim's wording; None if unclear."""
#     if not claim_wording:
#         return None
#     w = claim_wording.lower()
#     # percentage-point wording is more specific, so check it first
#     if any(term in w for term in _PP_WORDS):
#         return "pp"
#     if any(term in w for term in _REL_WORDS):
#         return "relative"
#     return None

# def _clean_zero(num):
#     return 0.0 if num == 0 else num

# def _both(a,b):
#     an, anote = parse_value(a)
#     bn, bnote = parse_value(b)
#     notes = [x for x in (anote, bnote) if x]
#     return an, bn, ("; ".join(notes) if notes else None)

# def _result(value, formula, inputs, notes = None):
#     return {
#         "value": None if value is None else round(value, 2),
#         "formula": formula,
#         "inputs": inputs,
#         "note": notes,
#     }

# def _join(a, b):
#     return f"{a}, {b}" if a else b

# def percentage_growth(old, new):
#     o, n, note = _both(old, new)
#     inputs = {"old" : o, "new" : n}
#     formula = "(new - old)/old*100"
#     if o is None or n is None:
#         return _result(None, formula, inputs, note or "undefined: could not parse input")
#     if o == 0:
#         return _result(None, formula, inputs, _join(note, "undefined: old value is 0"))
#     return _result((n - o)/o *100, formula, inputs, note)

# def absolute_change(old, new):
#     o, n, note  = _both(old, new)
#     inputs = {"old" : o, "new" : n}
#     formula = "new - old"
#     if o is None or n is None:
#         return _result(None, formula, inputs, note or "undefined: could not parse input")
#     return _result(n-o, formula, inputs, note)

# def margin(profit, revenue):
#     p, r, note = _both(profit, revenue)
#     inputs = {"profit" : p, "revenue" : r}
#     formula = "profit / revenue * 100"
#     if p is None or r is None:
#         return _result(None, formula, inputs, note or "undefined: could not parse input")
#     if r == 0:
#         return _result(None, formula, inputs, _join(note, "undefined revenue is 0"))
#     return _result(p/r * 100, formula, inputs, note)

# def cagr(beginning, ending, years):
#     b, e, note = _both(beginning, ending)
#     y, ynote = parse_value(years)
#     if ynote:
#         note = _join(note, ynote)
#     inputs = {"beginning": b, "ending": e, "years": y}   # y, not years
#     formula = "(ending/beginning)^(1/years) - 1"
#     if b is None or e is None or y is None:              # y, not years
#         return _result(None, formula, inputs, note or "undefined: could not parse input")
#     if b == 0:
#         return _result(None, formula, inputs, _join(note, "undefined: beginning value is 0"))
#     if y == 0:                                            # y, not years
#         return _result(None, formula, inputs, _join(note, "undefined: years is 0"))
#     ratio = e / b
#     if ratio < 0:
#         return _result(None, formula, inputs, _join(note, "undefined: negative ratio, cannot take root"))
#     return _result((ratio ** (1 / y) - 1) * 100, formula, inputs, note) 

# def percentage_point_change(old_margin, new_margin):
#     o, n, note = _both(old_margin, new_margin)
#     inputs = {"old_margin" : o, "new_margin" : n}
#     formula = "new_margin - old_margin"
#     if o is None or n is None:
#         return _result(None, formula, inputs, note or "undefined: dould not parse input")
#     return _result(n - o, formula, inputs, note)

# if __name__ == "__main__":
#     import json
#     tests = [
#         ("percentage_growth", percentage_growth("$10B", "$14B")),
#         ("absolute_change", absolute_change("$10B", "$14B")),
#         ("margin", margin("$30B", "$100B")),
#         ("cagr", cagr("$100B", "$200B", 5)),
#         ("percentage_point_change", percentage_point_change(25, 30)),
#         ("growth_from_zero", percentage_growth(0, 50)),
#         ("negative_parens", absolute_change("(500)", "300")),
#         ("percent_deferred", margin("30%", "100%")),
#         ("malformed_commas", absolute_change("1,2,3", "10")),
#     ]
#     for name, r in tests:
#         print(name)
#         print(json.dumps(r, indent = 2))
#         print()

