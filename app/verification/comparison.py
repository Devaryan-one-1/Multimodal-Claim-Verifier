REL_TOLERANCE = 0.05
ABS_FLOOR = 0.5

SUPPORTED = "SUPPORTED"
PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
CONTRADICTED = "CONTRADICTED"
UNKNOWN = "UNKNOWN"


def compare(claimed, actual, rel_tol=REL_TOLERANCE, abs_floor=ABS_FLOOR):
    """
    Compare a claimed numeric value against the deterministically
    calculated actual value.

    Possible verdicts:
        SUPPORTED
        PARTIALLY_SUPPORTED
        CONTRADICTED
        UNKNOWN
    """

    # Missing values -> cannot verify safely
    if actual is None or claimed is None:
        return {
            "claimed": claimed,
            "actual": actual,
            "difference": None,
            "tolerance": None,
            "verdict": UNKNOWN,
        }

    claimed = float(claimed)
    actual = float(actual)

    difference = claimed - actual
    distance = abs(difference)

    tolerance = max(
        rel_tol * abs(actual),
        abs_floor,
    )

    # Opposite directions are a clear contradiction.
    if _opposite_sign(claimed, actual):
        verdict = CONTRADICTED

    # Very close to the calculated value.
    elif distance <= tolerance:
        verdict = SUPPORTED

    # Close, but not close enough to call fully supported.
    elif distance <= 2 * tolerance:
        verdict = PARTIALLY_SUPPORTED

    # Substantially different.
    else:
        verdict = CONTRADICTED

    return {
        "claimed": round(claimed, 2),
        "actual": round(actual, 2),
        "difference": round(difference, 2),
        "tolerance": round(tolerance, 2),
        "verdict": verdict,
    }


def _opposite_sign(a, b):
    """
    Return True when the claimed and actual values represent
    opposite directions.
    """
    return (
        (a > 0 and b < 0)
        or
        (a < 0 and b > 0)
    )


if __name__ == "__main__":
    cases = [
        ("exact", compare(10, 10)),
        ("close", compare(10.3, 10)),
        ("partial", compare(11, 10)),
        ("wrong", compare(40, 10)),
        ("wrong direction", compare(-10, 10)),
        ("missing", compare(40, None)),
    ]

    for name, result in cases:
        print(name)
        print(result)
        print()
# REL_TOLERANCE = 0.05   # 5% of the actual value
# ABS_FLOOR = 0.5        # absolute floor so tiny actuals don't get a near-zero tolerance

# SUPPORTED = "SUPPORTED"
# PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
# MISLEADING = "MISLEADING"
# CONTRADICTED = "CONTRADICTED"
# INSUFFICIENT = "INSUFFICIENT"

# def compare(claimed, actual, rel_tol=REL_TOLERANCE, abs_floor=ABS_FLOOR):
#     """
#     claimed, actual: numbers (already parsed/normalized upstream).
#     Returns {claimed, actual, difference, verdict}.
#     """
#     # INSUFFICIENT: no usable actual (or claimed) value
#     if actual is None or claimed is None:
#         return {
#             "claimed": claimed,
#             "actual": actual,
#             "difference": None,
#             "verdict": INSUFFICIENT,
#         }

#     claimed = float(claimed)
#     actual = float(actual)
#     difference = round(claimed - actual, 2)
#     dist = abs(difference)

#     tolerance = max(rel_tol * abs(actual), abs_floor)

#     # wrong direction (opposite signs, both non-zero) -> always CONTRADICTED
#     if _opposite_sign(claimed, actual):
#         verdict = CONTRADICTED
#     elif dist <= tolerance:
#         verdict = SUPPORTED
#     elif dist <= 2 * tolerance:
#         verdict = PARTIALLY_SUPPORTED
#     else:
#         # far off, but same direction -> MISLEADING (inflates/deflates a real trend)
#         # unless it's so far it's effectively a different claim -> CONTRADICTED
#         verdict = MISLEADING if dist <= _misleading_ceiling(actual) else CONTRADICTED

#     return {
#         "claimed": round(claimed, 2),
#         "actual": round(actual, 2),
#         "difference": difference,
#         "verdict": verdict,
#     }


# def _opposite_sign(a, b):
#     """True if a and b point in opposite directions (both non-zero)."""
#     return (a > 0 and b < 0) or (a < 0 and b > 0)


# def _misleading_ceiling(actual):
#     """
#     Beyond 2x tolerance but same direction is MISLEADING up to this ceiling;
#     past it, the claim is so far from actual it's CONTRADICTED. Scales with
#     the magnitude of actual so it's meaningful for both 10% and $10B.
#     """
#     return max(abs(actual), ABS_FLOOR * 10)  # within ~100% of actual = misleading


# if __name__ == "__main__":
#     import json
#     cases = [
#         ("claimed 40 vs actual 10", compare(40, 10)),     # far, same dir -> MISLEADING/CONTRADICTED
#         ("claimed 10.2 vs actual 10", compare(10.2, 10)), # within tol -> SUPPORTED
#         ("claimed 11 vs actual 10", compare(11, 10)),     # ~2x tol -> PARTIALLY_SUPPORTED
#         ("claimed 15 vs actual 10", compare(15, 10)),     # same dir, far -> MISLEADING
#         ("claimed -5 vs actual 10", compare(-5, 10)),     # wrong direction -> CONTRADICTED
#         ("claimed 40 vs actual None", compare(40, None)), # no actual -> INSUFFICIENT
#     ]
#     for label, r in cases:
#         print(f"{label}: {r['verdict']}")
#         print(json.dumps(r, indent=2))
#         print()