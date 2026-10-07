LETTER_SCALE = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000, "t": 1_000_000_000_000}

WORD_SCALE = {
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
    "trillion": 1_000_000_000_000,
    # common abbreviations sometimes seen in filings
    "mn": 1_000_000,
    "bn": 1_000_000_000,
    "tn": 1_000_000_000_000,
}

CURRENCIES = {
    "$": "USD",
    "usd": "USD",
    "us$": "USD",
    "dollars": "USD",
    "dollar": "USD",
}

DEFAULT_CURRENCY = "USD"

def parse_number(text):
    if isinstance(text, (int, float)):
        return {"value": _clean_zero(float(text)), "currency": DEFAULT_CURRENCY}, \
               "no currency in input; assumed USD"
    if not isinstance(text, str):
        return None, f"unparseable type: {type(text).__name__}"

    s = text.strip()
    if not s:
        return None, "empty input"

    # percent is deferred elsewhere
    if "%" in s:
        return None, "percent value not handled here (deferred)"

    notes = []
    lower = s.lower()

    # 1. accounting negative: (500) -> -500
    negative = False
    if lower.startswith("(") and lower.endswith(")"):
        negative = True
        lower = lower[1:-1].strip()
        notes.append("parentheses interpreted as negative (accounting convention)")

    # 2. detect + strip currency (symbol or word/code), track whether we found one
    currency, lower, found_currency = _extract_currency(lower)
    if not found_currency:
        notes.append("no currency in input; assumed USD")

    # 3. split off a trailing word scale ('billion', 'mn', ...) if present
    scale = 1
    tokens = lower.split()
    if tokens and tokens[-1] in WORD_SCALE:
        scale = WORD_SCALE[tokens[-1]]
        tokens = tokens[:-1]
    lower = " ".join(tokens).strip()

    # 4. remove spaces so '$ 10' / '10 000' style collapses; strip commas
    compact = lower.replace(" ", "")
    if "," in compact:
        parts = compact.split(",")
        if len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
            compact = "".join(parts)
        else:
            return None, f"could not parse: {text!r} (malformed commas)"

    # 5. letter suffix (K/M/B/T) if a word scale wasn't already applied
    if scale == 1 and compact and compact[-1] in LETTER_SCALE:
        scale = LETTER_SCALE[compact[-1]]
        compact = compact[:-1]

    # 6. leading minus
    if compact.startswith("-"):
        negative = True
        compact = compact[1:]

    # 7. final numeric parse
    try:
        num = float(compact) * scale
    except ValueError:
        return None, f"could not parse: {text!r} (may need LLM normalization)"

    if negative:
        num = -num

    result = {"value": _clean_zero(round(num, 2)), "currency": currency}
    return result, ("; ".join(notes) if notes else None)

def parse_percent(text):
    """
    Parse a percentage into a TYPED value: {value, kind: 'percent'}.
      '30%'  -> {value: 30.0, kind: 'percent'}
      30     -> {value: 30.0, kind: 'percent'}  (bare number ASSUMED percent, noted)
    Returns (result, note); result is None if unparseable.
    """
    if isinstance(text, (int, float)):
        return {"value": _clean_zero(float(text)), "kind": "percent"}, \
               "bare number assumed to be percent"
    if not isinstance(text, str):
        return None, f"unparseable type: {type(text).__name__}"

    s = text.strip()
    if not s:
        return None, "empty input"

    had_percent = "%" in s
    s = s.replace("%", "").strip()
    try:
        val = float(s)
    except ValueError:
        return None, f"could not parse percent: {text!r}"

    note = None if had_percent else "bare number assumed to be percent"
    return {"value": _clean_zero(val), "kind": "percent"}, note


def to_ratio(typed):
    """
    Convert a typed percent (or bare number) to a ratio for math.
      {value: 30, kind: 'percent'} -> 0.30
      bare 30 (assumed percent)    -> 0.30
    Returns (ratio, note); None if unconvertible.
    """
    if isinstance(typed, dict):
        if typed.get("kind") == "percent" and typed.get("value") is not None:
            return typed["value"] / 100.0, None
        if typed.get("kind") == "ratio":
            return typed.get("value"), None
        return None, f"cannot convert typed value: {typed}"
    if isinstance(typed, (int, float)):
        return float(typed) / 100.0, "bare number assumed to be percent"
    return None, "not a convertible value"

def _extract_currency(lower):
    """
    Find and remove a currency token. Returns (currency_code, remaining, found).
    Checks multi-char tokens first so 'us$' beats '$'.
    """
    # word/code currencies (whole-word-ish): check longer keys first
    for key in sorted(CURRENCIES, key=len, reverse=True):
        if key.isalpha():
            # match as a separate token
            toks = lower.split()
            if key in toks:
                toks.remove(key)
                return CURRENCIES[key], " ".join(toks), True
        else:
            # symbol: can be stuck to the number
            if key in lower:
                return CURRENCIES[key], lower.replace(key, ""), True
    return DEFAULT_CURRENCY, lower, False


def _clean_zero(num):
    return 0.0 if num == 0 else num


if __name__ == "__main__":
    import json
    tests = ["$10B", "$10 billion", "$10,000M", "10 billion USD",
             "10 billion", "(500)", "1,234,567", "$5.5bn", "12", 12,
             "3 million dollars", "1,2,3", "40%"]
    for tst in tests:
        res, note = parse_number(tst)
        print(f"{str(tst):>18}  ->  {json.dumps(res)}   note: {note}")