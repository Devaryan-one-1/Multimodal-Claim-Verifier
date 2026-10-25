from __future__ import annotations

import math
import re
from decimal import Decimal, InvalidOperation

SCALES = {"k": 1000, "thousand": 1000, "thousands": 1000, "m": 10**6, "million": 10**6, "millions": 10**6, "b": 10**9, "bn": 10**9, "billion": 10**9, "billions": 10**9, "t": 10**12, "trillion": 10**12, "trillions": 10**12}
CURRENCY = r"USD|EUR|GBP|CAD|AUD|JPY|CNY|CHF|INR|HKD"


def parse_number(value, *, context: str = ""):
    if isinstance(value, bool) or value is None:
        return None, "Missing or invalid numeric value."
    original = str(value).strip()
    text = original.replace("−", "-").replace("\u00a0", " ")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    currency_matches = re.findall(rf"\b({CURRENCY})\b", text, re.I)
    context_currency = re.findall(rf"\b({CURRENCY})\b", context, re.I)
    codes = {x.upper() for x in currency_matches or context_currency}
    if len(codes) > 1:
        return None, "Ambiguous currency metadata."
    currency = next(iter(codes), None)
    if not currency:
        if "€" in text:
            currency = "EUR"
        elif "£" in text:
            currency = "GBP"
        elif re.search(r"u\.?s\.?\s*dollars?", context, re.I):
            currency = "USD"
    text = re.sub(rf"\b(?:{CURRENCY})\b", "", text, flags=re.I)
    text = text.replace("$", "").replace("€", "").replace("£", "").strip()
    bracketed = re.fullmatch(r"\(\s*([+-]?\d[\d,]*(?:\.\d+)?)\s*\)\s*([A-Za-z]+|%)?", text)
    if bracketed:
        negative = True
        text = bracketed.group(1) + (" " + bracketed.group(2) if bracketed.group(2) else "")
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    percent = "%" in text or bool(re.search(r"\bpercent\b", text, re.I))
    # A percentage column may contain bare numeric cells with an explicit % header.
    percent = percent or bool(re.fullmatch(r"\s*\(?\s*(?:%|percent|percentage)\s*\)?\s*", context, re.I))
    text = re.sub(r"%|\bpercent\b", "", text, flags=re.I).strip()
    match = re.fullmatch(r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+)\s*([a-zA-Z]+)?", text)
    if not match:
        return None, f"Cannot normalize {original!r} without guessing."
    numeral, suffix = match.groups()
    suffix = (suffix or "").lower()
    if suffix and suffix not in SCALES:
        return None, f"Unsupported unit suffix: {suffix}."
    context_scales = {SCALES[m.lower()] for m in re.findall(r"\b(thousands?|millions?|billions?|trillions?)\b", context, re.I)}
    if not suffix and not percent and len(context_scales) > 1:
        return None, "Ambiguous unit scale."
    scale = 1 if percent else SCALES.get(suffix, next(iter(context_scales), 1))
    try:
        number = Decimal(numeral.replace(",", "")) * scale
        if negative:
            number = -abs(number)
        normalized = float(number)
    except (InvalidOperation, OverflowError):
        return None, "Invalid numeric value."
    if not math.isfinite(normalized):
        return None, "Non-finite numeric value."
    precision = len(numeral.split(".")[1]) if "." in numeral else 0
    unit = "percent" if percent else (currency or ("amount" if any(x in original for x in "$€£") else "number"))
    return {
        "value": normalized, "currency": currency, "kind": "percent" if percent else "number",
        "unit": unit, "scale": scale, "precision": precision,
        "raw": original, "unit_context": context,
    }, None


def parse_percent(value):
    parsed, reason = parse_number(value)
    if parsed:
        parsed.update(kind="percent", unit="percent", currency=None, scale=1)
    return parsed, reason

# from __future__ import annotations

# import math
# import re
# from decimal import Decimal, InvalidOperation

# SCALES = {"k": 1000, "thousand": 1000, "thousands": 1000, "m": 10**6, "million": 10**6, "millions": 10**6, "b": 10**9, "bn": 10**9, "billion": 10**9, "billions": 10**9, "t": 10**12, "trillion": 10**12, "trillions": 10**12}
# CURRENCY = r"USD|EUR|GBP|CAD|AUD|JPY|CNY|CHF|INR|HKD"


# def parse_number(value, *, context: str = ""):
#     if isinstance(value, bool) or value is None:
#         return None, "Missing or invalid numeric value."
#     original = str(value).strip()
#     text = original.replace("−", "-").replace("\u00a0", " ")
#     negative = text.startswith("(") and text.endswith(")")
#     if negative:
#         text = text[1:-1].strip()
#     currency_matches = re.findall(rf"\b({CURRENCY})\b", text, re.I)
#     context_currency = re.findall(rf"\b({CURRENCY})\b", context, re.I)
#     codes = {x.upper() for x in currency_matches or context_currency}
#     if len(codes) > 1:
#         return None, "Ambiguous currency metadata."
#     currency = next(iter(codes), None)
#     if not currency:
#         if "€" in text:
#             currency = "EUR"
#         elif "£" in text:
#             currency = "GBP"
#         elif re.search(r"u\.?s\.?\s*dollars?", context, re.I):
#             currency = "USD"
#     text = re.sub(rf"\b(?:{CURRENCY})\b", "", text, flags=re.I)
#     text = text.replace("$", "").replace("€", "").replace("£", "").strip()
#     if text.startswith("(") and text.endswith(")"):
#         negative = True
#         text = text[1:-1].strip()
#     percent = "%" in text or bool(re.search(r"\bpercent\b", text, re.I))
#     # A percentage column may contain bare numeric cells with an explicit % header.
#     percent = percent or bool(re.fullmatch(r"\s*\(?\s*(?:%|percent|percentage)\s*\)?\s*", context, re.I))
#     text = re.sub(r"%|\bpercent\b", "", text, flags=re.I).strip()
#     match = re.fullmatch(r"([+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?|[+-]?\.\d+)\s*([a-zA-Z]+)?", text)
#     if not match:
#         return None, f"Cannot normalize {original!r} without guessing."
#     numeral, suffix = match.groups()
#     suffix = (suffix or "").lower()
#     if suffix and suffix not in SCALES:
#         return None, f"Unsupported unit suffix: {suffix}."
#     context_scales = {SCALES[m.lower()] for m in re.findall(r"\b(thousands?|millions?|billions?|trillions?)\b", context, re.I)}
#     if not suffix and not percent and len(context_scales) > 1:
#         return None, "Ambiguous unit scale."
#     scale = 1 if percent else SCALES.get(suffix, next(iter(context_scales), 1))
#     try:
#         number = Decimal(numeral.replace(",", "")) * scale
#         if negative:
#             number = -abs(number)
#         normalized = float(number)
#     except (InvalidOperation, OverflowError):
#         return None, "Invalid numeric value."
#     if not math.isfinite(normalized):
#         return None, "Non-finite numeric value."
#     precision = len(numeral.split(".")[1]) if "." in numeral else 0
#     unit = "percent" if percent else (currency or ("amount" if any(x in original for x in "$€£") else "number"))
#     return {
#         "value": normalized, "currency": currency, "kind": "percent" if percent else "number",
#         "unit": unit, "scale": scale, "precision": precision,
#         "raw": original, "unit_context": context,
#     }, None


# def parse_percent(value):
#     parsed, reason = parse_number(value)
#     if parsed:
#         parsed.update(kind="percent", unit="percent", currency=None, scale=1)
#     return parsed, reason

# # LETTER_SCALE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}

# # WORD_SCALE = {
# #     "thousand": 1_000,
# #     "million": 1_000_000,
# #     "billion": 1_000_000_000,
# #     "trillion": 1_000_000_000_000,
# #     # common abbreviations sometimes seen in filings
# #     "mn": 1_000_000,
# #     "bn": 1_000_000_000,
# #     "tn": 1_000_000_000_000,
# # }

# # CURRENCIES = {
# #     "$": "USD",
# #     "usd": "USD",
# #     "us$": "USD",
# #     "dollars": "USD",
# #     "dollar": "USD",
# # }

# # DEFAULT_CURRENCY = "USD"

# # def parse_number(text):
# #     if isinstance(text, (int, float)):
# #         return {"value": _clean_zero(float(text)), "currency": DEFAULT_CURRENCY}, \
# #                "no currency in input; assumed USD"
# #     if not isinstance(text, str):
# #         return None, f"unparseable type: {type(text).__name__}"

# #     s = text.strip()
# #     if not s:
# #         return None, "empty input"

# #     # percent is deferred elsewhere
# #     if "%" in s:
# #         return None, "percent value not handled here (deferred)"

# #     notes = []
# #     lower = s.lower()

# #     # 1. accounting negative: (500) -> -500
# #     negative = False
# #     if lower.startswith("(") and lower.endswith(")"):
# #         negative = True
# #         lower = lower[1:-1].strip()
# #         notes.append("parentheses interpreted as negative (accounting convention)")

# #     # 2. detect + strip currency (symbol or word/code), track whether we found one
# #     currency, lower, found_currency = _extract_currency(lower)
# #     if not found_currency:
# #         notes.append("no currency in input; assumed USD")

# #     # 3. split off a trailing word scale ('billion', 'mn', ...) if present
# #     scale = 1
# #     tokens = lower.split()
# #     if tokens and tokens[-1] in WORD_SCALE:
# #         scale = WORD_SCALE[tokens[-1]]
# #         tokens = tokens[:-1]
# #     lower = " ".join(tokens).strip()

# #     # 4. remove spaces so '$ 10' / '10 000' style collapses; strip commas
# #     compact = lower.replace(" ", "")
# #     if "," in compact:
# #         parts = compact.split(",")
# #         if len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
# #             compact = "".join(parts)
# #         else:
# #             return None, f"could not parse: {text!r} (malformed commas)"

# #     # 5. letter suffix (K/M/B/T) if a word scale wasn't already applied
# #     if scale == 1 and compact and compact[-1] in LETTER_SCALE:
# #         scale = LETTER_SCALE[compact[-1]]
# #         compact = compact[:-1]

# #     # 6. leading minus
# #     if compact.startswith("-"):
# #         negative = True
# #         compact = compact[1:]

# #     # 7. final numeric parse
# #     try:
# #         num = float(compact) * scale
# #     except ValueError:
# #         return None, f"could not parse: {text!r} (may need LLM normalization)"

# #     if negative:
# #         num = -num

# #     result = {"value": _clean_zero(round(num, 2)), "currency": currency}
# #     return result, ("; ".join(notes) if notes else None)

# # def parse_percent(text):
# #     """
# #     Parse a percentage into a TYPED value: {value, kind: 'percent'}.
# #       '30%'  -> {value: 30.0, kind: 'percent'}
# #       30     -> {value: 30.0, kind: 'percent'}  (bare number ASSUMED percent, noted)
# #     Returns (result, note); result is None if unparseable.
# #     """
# #     if isinstance(text, (int, float)):
# #         return {"value": _clean_zero(float(text)), "kind": "percent"}, \
# #                "bare number assumed to be percent"
# #     if not isinstance(text, str):
# #         return None, f"unparseable type: {type(text).__name__}"

# #     s = text.strip()
# #     if not s:
# #         return None, "empty input"

# #     had_percent = "%" in s
# #     s = s.replace("%", "").strip()
# #     try:
# #         val = float(s)
# #     except ValueError:
# #         return None, f"could not parse percent: {text!r}"

# #     note = None if had_percent else "bare number assumed to be percent"
# #     return {"value": _clean_zero(val), "kind": "percent"}, note


# # def to_ratio(typed):
# #     """
# #     Convert a typed percent (or bare number) to a ratio for math.
# #       {value: 30, kind: 'percent'} -> 0.30
# #       bare 30 (assumed percent)    -> 0.30
# #     Returns (ratio, note); None if unconvertible.
# #     """
# #     if isinstance(typed, dict):
# #         if typed.get("kind") == "percent" and typed.get("value") is not None:
# #             return typed["value"] / 100.0, None
# #         if typed.get("kind") == "ratio":
# #             return typed.get("value"), None
# #         return None, f"cannot convert typed value: {typed}"
# #     if isinstance(typed, (int, float)):
# #         return float(typed) / 100.0, "bare number assumed to be percent"
# #     return None, "not a convertible value"

# # def _extract_currency(lower):
# #     """
# #     Find and remove a currency token. Returns (currency_code, remaining, found).
# #     Checks multi-char tokens first so 'us$' beats '$'.
# #     """
# #     # word/code currencies (whole-word-ish): check longer keys first
# #     for key in sorted(CURRENCIES, key=len, reverse=True):
# #         if key.isalpha():
# #             # match as a separate token
# #             toks = lower.split()
# #             if key in toks:
# #                 toks.remove(key)
# #                 return CURRENCIES[key], " ".join(toks), True
# #         else:
# #             # symbol: can be stuck to the number
# #             if key in lower:
# #                 return CURRENCIES[key], lower.replace(key, ""), True
# #     return DEFAULT_CURRENCY, lower, False


# # def _clean_zero(num):
# #     return 0.0 if num == 0 else num


# # if __name__ == "__main__":
# #     import json
# #     tests = ["$10B", "$10 billion", "$10,000M", "10 billion USD",
# #              "10 billion", "(500)", "1,234,567", "$5.5bn", "12", 12,
# #              "3 million dollars", "1,2,3", "40%"]
# #     for tst in tests:
# #         res, note = parse_number(tst)
# #         print(f"{str(tst):>18}  ->  {json.dumps(res)}   note: {note}")