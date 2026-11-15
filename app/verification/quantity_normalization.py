"""Normalize retained word quantities only at the calculation boundary."""
import re


def calculation_literal(literal, unit):
    text = str(literal or "")
    words = re.sub(r"\s+", " ", text.casefold().strip())
    fractions = {"half": "50%", "a half": "50%", "one half": "50%",
                 "a quarter": "25%", "one quarter": "25%", "three quarters": "75%"}
    if unit == "percent" and words in fractions:
        return fractions[words]
    return text
