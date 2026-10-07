from number_parser import (parse_number, LETTER_SCALE, WORD_SCALE, CURRENCIES, DEFAULT_CURRENCY)

def normalize(text):
    """Normalize a value string to {value, currency} (or None). Returns just
    the result dict; use parse_number directly if you also want the note."""
    result, _note = parse_number(text)
    return result


def normalize_with_note(text):
    """Same as normalize but also returns the explanatory note."""
    return parse_number(text)


__all__ = ["normalize", "normalize_with_note", "parse_number",
           "LETTER_SCALE", "WORD_SCALE", "CURRENCIES", "DEFAULT_CURRENCY"]