from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, localcontext
from typing import Any

import streamlit as st


# Keys match the formula strings produced by financial_calculations.py.
FORMULAS = {
    "(new-old)/old*100": (
        "Percentage growth", "percent", ("old", "new"),
        "(New value − Old value) ÷ Old value × 100",
        "({new} − {old}) ÷ {old} × 100",
    ),
    "new-old": (
        "Absolute change", "amount", ("old", "new"),
        "New value − Old value", "{new} − {old}",
    ),
    "profit/revenue*100": (
        "Margin", "percent", ("profit", "revenue"),
        "Profit ÷ Revenue × 100", "{profit} ÷ {revenue} × 100",
    ),
    "(ending/beginning)^(1/years)-1": (
        "CAGR", "percent", ("beginning", "ending", "years"),
        "((Ending ÷ Beginning)^(1 ÷ Years) − 1) × 100",
        "(({ending} ÷ {beginning})^(1 ÷ {years}) − 1) × 100",
    ),
    "new_margin-old_margin": (
        "Percentage-point change", "percentage points",
        ("old_margin", "new_margin"),
        "New margin − Old margin", "{new_margin} − {old_margin}",
    ),
}


def render_calculation(result: dict[str, Any]) -> None:
    """Render backend calculations; never generate or change a verdict.

    Accepts both claim_verifier.py results and verdict.normalize_result()
    output. Optional metadata belongs inside calculation:

        raw_inputs: {"old": "$10.00B", "new": "$11.00B"}
        input_metadata: {"old": {"currency": "USD", "unit": "USD"}}
        result_precision: 2

    Precision metadata controls display only. Missing source strings are
    reported explicitly rather than reconstructed from normalized numbers.
    """
    raw = _mapping(result.get("raw"))
    calculation = _mapping(_first(result.get("calculation"), raw.get("calculation")))
    comparison = _mapping(_first(result.get("comparison"), raw.get("comparison")))
    parsed = _mapping(_first(result.get("parsed_claim"), raw.get("parsed_claim")))
    verdict = _first(result.get("verdict"), comparison.get("verdict"), "UNKNOWN")
    verdict = str(getattr(verdict, "value", verdict)).upper()

    with st.expander("Calculation", expanded=verdict == "CONTRADICTED"):
        if not calculation:
            _unavailable(_reason(calculation, raw, result))
            return

        inputs = _mapping(calculation.get("inputs"))
        original = _raw_inputs(calculation, raw, result)
        margin_analysis = "pp_change" in calculation or "relative_change" in calculation
        formula = str(_first(calculation.get("formula"), result.get("formula"), ""))
        spec = FORMULAS.get(re.sub(r"\s+", "", formula.lower()))
        currency = _currency(calculation, raw, result)

        _render_inputs(inputs, original, calculation, currency, spec, margin_analysis)

        if margin_analysis:
            _render_margin_analysis(calculation, inputs, comparison, parsed, result, currency)
            _render_note(calculation)
            return

        if _decimal(calculation.get("value")) is None:
            _unavailable(_reason(calculation, raw, result))
            return

        if formula:
            st.markdown("#### Formula")
            st.code(spec[3] if spec else formula, language=None)
        if spec and all(_decimal(inputs.get(name)) is not None for name in spec[2]):
            values = {name: _number(inputs[name]) for name in spec[2]}
            st.markdown("#### Substitution using normalized values")
            st.code(spec[4].format(**values), language=None)
        elif formula:
            st.caption("A substitution cannot be shown without all numerical inputs.")

        unit = spec[1] if spec else _unit(_first(calculation.get("result_unit"), result.get("unit"), parsed.get("unit")))
        if unit == "amount":
            unit = _unit(calculation.get("result_unit")) or currency
        claimed = _first(result.get("claimed_value"), result.get("claimed"), comparison.get("claimed"), parsed.get("claimed_change"))
        actual = _first(result.get("actual_value"), result.get("actual"), comparison.get("actual"), calculation.get("value"))
        _render_comparison(claimed, actual, unit, currency, calculation, comparison, result)
        if spec:
            st.caption(f"Calculation type: {spec[0]}")
        _render_note(calculation)


def _render_inputs(inputs, original, calculation, currency, spec, margin_analysis):
    st.markdown("#### Input values")
    keys = [key for key in dict.fromkeys([*inputs, *original]) if key not in {"unit", "currency", "precision"}]
    if not keys:
        st.caption("No input values were returned by the backend.")
        return
    metadata = _mapping(calculation.get("input_metadata"))
    for key in keys:
        value = inputs.get(key)
        source = original.get(key)
        details = {**_mapping(value), **_mapping(metadata.get(key))}
        percent_input = margin_analysis or key in {"old_margin", "new_margin"} or _unit(details.get("kind")) == "percent"
        percent_input = percent_input or _unit(details.get("unit")) == "percent"
        percent_input = percent_input or (isinstance(source, str) and "%" in source)
        if key == "years":
            input_currency, input_unit = None, "years"
        elif percent_input:
            input_currency, input_unit = None, "percent"
        else:
            input_currency = _first(details.get("currency"), _mapping(source).get("currency"), currency)
            input_unit = _unit(_first(details.get("unit"), calculation.get("input_unit"), inputs.get("unit")))
            input_unit = input_unit or input_currency
        left, right = st.columns(2)
        label = str(key).replace("_", " ").title()
        left.markdown(f"**{label} — original document value**")
        left.text(_original(source) if source is not None else "Not retained by the current backend")
        right.markdown(f"**{label} — normalized value**")
        right.text(_format(value, input_unit, _first(details.get("normalized_precision"), details.get("precision"))))
        st.caption(
            f"Currency: {input_currency or 'Not supplied / not applicable'} · "
            f"Input unit: {input_unit or 'Not supplied'} · Source scale: ×{details.get('scale', 1):,}"
        )
        if details.get("unit_context"):
            st.caption(f"Source unit statement: {details['unit_context']}")


def _render_margin_analysis(calculation, inputs, comparison, parsed, result, currency):
    st.markdown("#### Margin change interpretations")
    pp = calculation.get("pp_change")
    relative = calculation.get("relative_change")
    if _decimal(pp) is None and _decimal(relative) is None:
        _unavailable(str(calculation.get("note") or "Neither margin calculation returned a finite value."))
        return
    left, right = st.columns(2)
    precision = calculation.get("result_precision")
    left.metric("Percentage-point change", _format(pp, "percentage points", precision))
    right.metric("Relative percentage change", _format(relative, "percent", precision))
    old, new = inputs.get("old"), inputs.get("new")
    if _decimal(old) is not None and _decimal(new) is not None:
        left.code(f"{_number(new)} − {_number(old)}", language=None)
        if _decimal(old) != 0:
            right.code(f"({_number(new)} − {_number(old)}) ÷ {_number(old)} × 100", language=None)
        else:
            right.caption("Relative change is undefined because the old margin is zero.")
    intended = calculation.get("likely_intended")
    if intended not in {"pp", "relative"}:
        st.warning("The backend has not selected a margin interpretation. Both calculations are shown; no comparison is inferred.")
        return
    actual = pp if intended == "pp" else relative
    unit = "percentage points" if intended == "pp" else "percent"
    st.caption(f"Backend-selected interpretation: {unit}.")
    if _decimal(actual) is None:
        _unavailable("The selected margin interpretation is undefined.")
        return
    claimed = _first(result.get("claimed_value"), comparison.get("claimed"), parsed.get("claimed_change"))
    _render_comparison(claimed, actual, unit, None, calculation, comparison, result)


def _render_comparison(claimed, actual, unit, currency, calculation, comparison, result):
    st.markdown("#### Result")
    precision = calculation.get("result_precision")
    claimed_precision = _first(result.get("claimed_precision"), comparison.get("claimed_precision"))
    actual_precision = _first(result.get("actual_precision"), comparison.get("actual_precision"), precision)
    difference_precision = _first(result.get("difference_precision"), comparison.get("difference_precision"), precision)
    difference = _first(result.get("difference"), comparison.get("difference"))
    if _decimal(difference) is None:
        c, a = _decimal(claimed), _decimal(actual)
        difference = c - a if c is not None and a is not None else None
    difference_unit = "percentage points" if _unit(unit) in {"percent", "percentage points"} else unit
    actual_col, claimed_col, difference_col = st.columns(3)
    actual_col.metric("Actual", _format(actual, unit, actual_precision))
    claimed_col.metric("Claimed", _format(claimed, unit, claimed_precision))
    difference_col.metric("Signed difference", _format(difference, difference_unit, difference_precision, signed=True))
    st.caption("Signed difference = claimed − actual. Positive means the claim is higher; negative means it is lower.")
    st.caption(f"Result unit: {_unit(unit) or 'Not supplied'}")
    operator = comparison.get("operator", "equal")
    if operator != "equal":
        st.caption("Claim comparison: actual " + {"greater_than": ">", "at_least": "≥", "less_than": "<", "at_most": "≤"}.get(operator, operator) + " claimed threshold.")
    if comparison.get("tolerance") is not None and operator == "equal":
        st.caption("Comparison tolerance: " + _format(comparison["tolerance"], difference_unit)
                   + " (based on displayed numeric precision).")


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    return next((value for value in values if value is not None), None)


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, dict):
        value = value.get("value")
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _number(value: Any, precision: Any = None, signed: bool = False) -> str:
    number = _decimal(value)
    if number is None:
        return "—"
    if number == 0:
        number = abs(number)
    if isinstance(precision, int) and not isinstance(precision, bool) and 0 <= precision <= 12:
        with localcontext() as context:
            context.prec = max(28, len(number.as_tuple().digits) + abs(number.adjusted()) + precision + 5)
            number = number.quantize(Decimal(1).scaleb(-precision))
        text = format(number, f",.{precision}f")
    else:
        text = format(number, ",f")
        # Preserve explicit string/Decimal precision. Floats cannot retain
        # the source's trailing zeros; show those in the original column.
        original = value.get("value") if isinstance(value, dict) else value
        if not isinstance(original, (str, Decimal)) and "." in text:
            text = text.rstrip("0").rstrip(".")
    if signed and number > 0:
        text = "+" + text
    return text.replace("-", "−")


def _unit(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    key = text.lower().replace("_", " ").replace("-", " ")
    if key in {"%", "percent", "percentage"}:
        return "percent"
    if key in {"pp", "ppt", "point", "points", "percentage point", "percentage points"}:
        return "percentage points"
    return text


def _format(value: Any, unit: Any, precision: Any = None, signed: bool = False) -> str:
    text = _number(value, precision, signed)
    if text == "—":
        return text
    unit = _unit(unit)
    if unit == "percent":
        return text + "%"
    return f"{text} {unit}" if unit else text


def _original(value: Any) -> str:
    if isinstance(value, dict):
        value = _first(value.get("display_value"), value.get("raw"), value.get("original"), value.get("text"), value.get("value"))
    return str(value) if value is not None else "Not retained by the current backend"


def _raw_inputs(calculation, raw, result):
    for source in [calculation, raw, result]:
        for key in ["raw_inputs", "source_values", "original_values"]:
            candidate = _mapping(source.get(key))
            if candidate:
                return candidate
    old = _first(raw.get("old_raw"), result.get("old_raw"))
    new = _first(raw.get("new_raw"), result.get("new_raw"))
    return {"old": old, "new": new} if old is not None or new is not None else {}


def _currency(*sources):
    return _first(*(source.get("currency") for source in sources))


def _reason(calculation, raw, result):
    note = calculation.get("note")
    if note:
        return str(note)
    for source in [raw, result]:
        for key in ["calculation_unavailable_reason", "error", "notes", "reason"]:
            value = source.get(key)
            if isinstance(value, list):
                value = " ".join(str(item) for item in value if item)
            if value:
                return str(value)
    return "No finite deterministic calculation was returned by the backend."


def _unavailable(reason):
    st.warning("Calculation unavailable")
    st.caption(reason)


def _render_note(calculation):
    if calculation.get("note"):
        st.info(str(calculation["note"]))

# from __future__ import annotations

# from typing import Any

# import streamlit as st


# def render_calculation(
#     result: dict[str, Any],
# ) -> None:
#     """
#     Display an auditable deterministic calculation.

#     Supports the dictionaries returned by financial_calculations.py:
#     - percentage_growth
#     - absolute_change
#     - margin
#     - CAGR
#     - percentage-point change
#     - analyze_change
#     """

#     calculation = result.get("calculation") or {}
#     raw_result = result.get("raw") or {}

#     expanded = result.get("verdict") == "CONTRADICTED"

#     with st.expander(
#         "Calculation",
#         expanded=expanded,
#     ):
#         if not calculation:
#             render_unavailable(
#                 find_unavailable_reason(raw_result)
#             )
#             return

#         if is_margin_analysis(calculation):
#             render_margin_analysis(
#                 calculation=calculation,
#                 result=result,
#             )
#             return

#         inputs = calculation.get("inputs") or {}
#         raw_inputs = find_raw_inputs(
#             calculation=calculation,
#             raw_result=raw_result,
#         )

#         render_value_inputs(
#             normalized_inputs=inputs,
#             raw_inputs=raw_inputs,
#             result=result,
#         )

#         calculated_value = calculation.get("value")
#         formula = calculation.get("formula")

#         if calculated_value is None:
#             render_unavailable(
#                 calculation.get("note")
#                 or "The calculation returned no numerical result."
#             )
#             return

#         if formula:
#             st.markdown("#### Formula")
#             st.code(
#                 display_formula(formula),
#                 language=None,
#             )

#             substituted = substitute_formula(
#                 formula=formula,
#                 inputs=inputs,
#             )

#             if substituted:
#                 st.markdown("#### Substitution")
#                 st.code(
#                     substituted,
#                     language=None,
#                 )

#         render_calculation_comparison(
#             result=result,
#             calculated_value=calculated_value,
#             calculation=calculation,
#         )

#         if calculation.get("note"):
#             st.info(str(calculation["note"]))


# def render_value_inputs(
#     normalized_inputs: dict[str, Any],
#     raw_inputs: dict[str, Any],
#     result: dict[str, Any],
# ) -> None:
#     st.markdown("#### Input values")

#     input_names = list(
#         dict.fromkeys(
#             [
#                 *raw_inputs.keys(),
#                 *normalized_inputs.keys(),
#             ]
#         )
#     )

#     if not input_names:
#         st.caption("No calculation inputs were returned.")
#         return

#     currency = find_currency(
#         result=result,
#         raw_inputs=raw_inputs,
#     )
#     unit = find_unit(result)

#     for input_name in input_names:
#         original_value = raw_inputs.get(input_name)
#         normalized_value = normalized_inputs.get(input_name)

#         col1, col2 = st.columns(2)

#         col1.markdown(
#             f"**{humanize(input_name)} — original**"
#         )
#         col1.write(
#             preserve_precision(original_value)
#             if original_value is not None
#             else "Not retained by the current backend"
#         )

#         col2.markdown(
#             f"**{humanize(input_name)} — normalized**"
#         )
#         col2.write(
#             format_normalized_value(
#                 normalized_value,
#                 currency=currency,
#                 unit=unit,
#             )
#         )

#     metadata = []

#     if currency:
#         metadata.append(f"Currency: **{currency}**")

#     if unit:
#         metadata.append(f"Unit: **{unit}**")

#     if metadata:
#         st.caption(" · ".join(metadata))


# def render_calculation_comparison(
#     result: dict[str, Any],
#     calculated_value: Any,
#     calculation: dict[str, Any],
# ) -> None:
#     claimed = result.get("claimed_value")
#     actual = first_not_none(
#         result.get("actual_value"),
#         calculated_value,
#     )
#     difference = result.get("difference")
#     unit = find_unit(result)

#     st.markdown("#### Result")

#     actual_col, claimed_col, difference_col = st.columns(3)

#     actual_col.metric(
#         "Actual",
#         format_result_value(actual, unit),
#     )

#     claimed_col.metric(
#         "Claimed",
#         format_result_value(claimed, unit),
#     )

#     difference_col.metric(
#         "Signed difference",
#         format_signed_difference(
#             difference,
#             unit,
#         ),
#     )

#     formula_name = identify_calculation(
#         calculation.get("formula")
#     )

#     if formula_name:
#         st.caption(f"Calculation type: {formula_name}")


# def render_margin_analysis(
#     calculation: dict[str, Any],
#     result: dict[str, Any],
# ) -> None:
#     """
#     Handle analyze_change(), which returns both percentage-point
#     and relative-percentage interpretations.
#     """

#     inputs = calculation.get("inputs") or {}
#     old_value = inputs.get("old")
#     new_value = inputs.get("new")

#     st.markdown("#### Margin values")

#     old_col, new_col = st.columns(2)

#     old_col.metric(
#         "Old margin",
#         format_result_value(old_value, "percent"),
#     )
#     new_col.metric(
#         "New margin",
#         format_result_value(new_value, "percent"),
#     )

#     st.markdown("#### Two valid interpretations")

#     pp_change = calculation.get("pp_change")
#     relative_change = calculation.get("relative_change")
#     intended = calculation.get("likely_intended")

#     pp_col, relative_col = st.columns(2)

#     pp_col.metric(
#         "Percentage-point change",
#         format_result_value(
#             pp_change,
#             "percentage points",
#         ),
#     )
#     pp_col.code(
#         f"{preserve_precision(new_value)} - "
#         f"{preserve_precision(old_value)}",
#         language=None,
#     )

#     relative_col.metric(
#         "Relative percentage change",
#         format_result_value(
#             relative_change,
#             "percent",
#         ),
#     )
#     relative_col.code(
#         (
#             f"({preserve_precision(new_value)} - "
#             f"{preserve_precision(old_value)}) / "
#             f"{preserve_precision(old_value)} × 100"
#         ),
#         language=None,
#     )

#     if intended == "pp":
#         st.success(
#             "The claim wording indicates a percentage-point change."
#         )
#     elif intended == "relative":
#         st.success(
#             "The claim wording indicates a relative percentage change."
#         )
#     else:
#         st.warning(
#             "The claim wording does not clearly distinguish percentage "
#             "change from percentage-point change."
#         )

#     claimed = result.get("claimed_value")
#     difference = result.get("difference")

#     comparison_col1, comparison_col2 = st.columns(2)

#     comparison_col1.metric(
#         "Claimed",
#         format_result_value(
#             claimed,
#             find_unit(result),
#         ),
#     )

#     comparison_col2.metric(
#         "Signed difference",
#         format_signed_difference(
#             difference,
#             find_unit(result),
#         ),
#     )

#     if calculation.get("note"):
#         st.info(str(calculation["note"]))


# def render_unavailable(reason: str) -> None:
#     st.warning("Calculation unavailable")
#     st.caption(
#         reason
#         or "No deterministic calculation was returned."
#     )


# def find_raw_inputs(
#     calculation: dict[str, Any],
#     raw_result: dict[str, Any],
# ) -> dict[str, Any]:
#     """
#     Look for original document strings without inventing them.

#     Recommended future pipeline field:

#         calculation["raw_inputs"] = {
#             "old": "$10B",
#             "new": "$11B"
#         }
#     """

#     candidates = [
#         calculation.get("raw_inputs"),
#         raw_result.get("raw_inputs"),
#         raw_result.get("source_values"),
#         raw_result.get("original_values"),
#     ]

#     for candidate in candidates:
#         if isinstance(candidate, dict) and candidate:
#             return candidate

#     old_raw = raw_result.get("old_raw")
#     new_raw = raw_result.get("new_raw")

#     if old_raw is not None or new_raw is not None:
#         return {
#             "old": old_raw,
#             "new": new_raw,
#         }

#     return {}


# def find_currency(
#     result: dict[str, Any],
#     raw_inputs: dict[str, Any],
# ) -> str | None:
#     raw_result = result.get("raw") or {}
#     calculation = result.get("calculation") or {}

#     currency = first_not_none(
#         result.get("currency"),
#         raw_result.get("currency"),
#         calculation.get("currency"),
#     )

#     if currency:
#         return str(currency)

#     for value in raw_inputs.values():
#         if isinstance(value, dict) and value.get("currency"):
#             return str(value["currency"])

#     return None


# def find_unit(result: dict[str, Any]) -> str | None:
#     raw_result = result.get("raw") or {}
#     parsed_claim = raw_result.get("parsed_claim") or {}

#     unit = first_not_none(
#         result.get("unit"),
#         raw_result.get("unit"),
#         parsed_claim.get("unit"),
#     )

#     return str(unit) if unit else None


# def find_unavailable_reason(
#     raw_result: dict[str, Any],
# ) -> str:
#     notes = raw_result.get("notes")

#     if isinstance(notes, list) and notes:
#         return " ".join(
#             str(note)
#             for note in notes
#             if note
#         )

#     if isinstance(notes, str) and notes:
#         return notes

#     return (
#         raw_result.get("reason")
#         or raw_result.get("error")
#         or "No deterministic calculation was returned."
#     )


# def display_formula(formula: str) -> str:
#     replacements = {
#         "(new - old)/old*100": (
#             "(New value − Old value) ÷ Old value × 100"
#         ),
#         "new - old": "New value − Old value",
#         "profit / revenue * 100": (
#             "Profit ÷ Revenue × 100"
#         ),
#         "(ending/beginning)^(1/years) - 1": (
#             "((Ending ÷ Beginning)^(1 ÷ Years) − 1) × 100"
#         ),
#         "new_margin - old_margin": (
#             "New margin − Old margin"
#         ),
#     }

#     return replacements.get(formula, formula)


# def substitute_formula(
#     formula: str,
#     inputs: dict[str, Any],
# ) -> str | None:
#     if not inputs:
#         return None

#     if formula == "(new - old)/old*100":
#         old = preserve_precision(inputs.get("old"))
#         new = preserve_precision(inputs.get("new"))
#         return f"({new} − {old}) ÷ {old} × 100"

#     if formula == "new - old":
#         old = preserve_precision(inputs.get("old"))
#         new = preserve_precision(inputs.get("new"))
#         return f"{new} − {old}"

#     if formula == "profit / revenue * 100":
#         profit = preserve_precision(inputs.get("profit"))
#         revenue = preserve_precision(inputs.get("revenue"))
#         return f"{profit} ÷ {revenue} × 100"

#     if formula == "(ending/beginning)^(1/years) - 1":
#         beginning = preserve_precision(
#             inputs.get("beginning")
#         )
#         ending = preserve_precision(
#             inputs.get("ending")
#         )
#         years = preserve_precision(
#             inputs.get("years")
#         )

#         return (
#             f"(({ending} ÷ {beginning})"
#             f"^(1 ÷ {years}) − 1) × 100"
#         )

#     if formula == "new_margin - old_margin":
#         old_margin = preserve_precision(
#             inputs.get("old_margin")
#         )
#         new_margin = preserve_precision(
#             inputs.get("new_margin")
#         )
#         return f"{new_margin} − {old_margin}"

#     return None


# def identify_calculation(
#     formula: str | None,
# ) -> str | None:
#     names = {
#         "(new - old)/old*100": "Percentage growth",
#         "new - old": "Absolute change",
#         "profit / revenue * 100": "Margin",
#         "(ending/beginning)^(1/years) - 1": "CAGR",
#         "new_margin - old_margin": (
#             "Percentage-point change"
#         ),
#     }

#     return names.get(formula)


# def is_margin_analysis(
#     calculation: dict[str, Any],
# ) -> bool:
#     return (
#         "pp_change" in calculation
#         or "relative_change" in calculation
#     )


# def format_normalized_value(
#     value: Any,
#     currency: str | None,
#     unit: str | None,
# ) -> str:
#     if value is None:
#         return "—"

#     if isinstance(value, dict):
#         currency = value.get("currency") or currency
#         value = value.get("value")

#     text = preserve_precision(value)

#     if currency:
#         return f"{text} {currency}"

#     if unit:
#         return f"{text} {unit}"

#     return text


# def format_result_value(
#     value: Any,
#     unit: str | None,
# ) -> str:
#     if value is None:
#         return "—"

#     text = preserve_precision(value)
#     unit_lower = str(unit or "").lower()

#     if unit_lower in {
#         "percent",
#         "percentage",
#         "%",
#     }:
#         return f"{text}%"

#     if unit_lower in {
#         "percentage point",
#         "percentage points",
#         "pp",
#         "ppt",
#     }:
#         return f"{text} percentage points"

#     return text


# def format_signed_difference(
#     value: Any,
#     unit: str | None,
# ) -> str:
#     if value is None:
#         return "—"

#     try:
#         number = float(value)
#     except (TypeError, ValueError):
#         return str(value)

#     sign = "+" if number > 0 else ""
#     text = f"{sign}{preserve_precision(number)}"

#     unit_lower = str(unit or "").lower()

#     if unit_lower in {
#         "percent",
#         "percentage",
#         "%",
#         "percentage point",
#         "percentage points",
#         "pp",
#         "ppt",
#     }:
#         return f"{text} percentage points"

#     return text


# def preserve_precision(value: Any) -> str:
#     """
#     Preserve the precision already present in document strings.

#     Numbers calculated by financial_calculations.py have already been
#     rounded there, so this function does not add unnecessary decimals.
#     """

#     if value is None:
#         return "—"

#     if isinstance(value, dict):
#         value = value.get("value")

#     if isinstance(value, str):
#         return value

#     if isinstance(value, int):
#         return f"{value:,}"

#     if isinstance(value, float):
#         if value.is_integer():
#             return f"{int(value):,}"

#         return f"{value:,}".rstrip("0").rstrip(".")

#     return str(value)


# def humanize(value: str) -> str:
#     return value.replace("_", " ").strip().title()


# def first_not_none(*values: Any) -> Any:
#     for value in values:
#         if value is not None:
#             return value

#     return None
# from __future__ import annotations

# import re
# from decimal import Decimal, InvalidOperation, localcontext
# from typing import Any

# import streamlit as st


# # Keys match the formula strings produced by financial_calculations.py.
# FORMULAS = {
#     "(new-old)/old*100": (
#         "Percentage growth", "percent", ("old", "new"),
#         "(New value − Old value) ÷ Old value × 100",
#         "({new} − {old}) ÷ {old} × 100",
#     ),
#     "new-old": (
#         "Absolute change", "amount", ("old", "new"),
#         "New value − Old value", "{new} − {old}",
#     ),
#     "profit/revenue*100": (
#         "Margin", "percent", ("profit", "revenue"),
#         "Profit ÷ Revenue × 100", "{profit} ÷ {revenue} × 100",
#     ),
#     "(ending/beginning)^(1/years)-1": (
#         "CAGR", "percent", ("beginning", "ending", "years"),
#         "((Ending ÷ Beginning)^(1 ÷ Years) − 1) × 100",
#         "(({ending} ÷ {beginning})^(1 ÷ {years}) − 1) × 100",
#     ),
#     "new_margin-old_margin": (
#         "Percentage-point change", "percentage points",
#         ("old_margin", "new_margin"),
#         "New margin − Old margin", "{new_margin} − {old_margin}",
#     ),
# }


# def render_calculation(result: dict[str, Any]) -> None:
#     """Render backend calculations; never generate or change a verdict.

#     Accepts both claim_verifier.py results and verdict.normalize_result()
#     output. Optional metadata belongs inside calculation:

#         raw_inputs: {"old": "$10.00B", "new": "$11.00B"}
#         input_metadata: {"old": {"currency": "USD", "unit": "USD"}}
#         result_precision: 2

#     Precision metadata controls display only. Missing source strings are
#     reported explicitly rather than reconstructed from normalized numbers.
#     """
#     raw = _mapping(result.get("raw"))
#     calculation = _mapping(_first(result.get("calculation"), raw.get("calculation")))
#     comparison = _mapping(_first(result.get("comparison"), raw.get("comparison")))
#     parsed = _mapping(_first(result.get("parsed_claim"), raw.get("parsed_claim")))
#     verdict = _first(result.get("verdict"), comparison.get("verdict"), "UNKNOWN")
#     verdict = str(getattr(verdict, "value", verdict)).upper()

#     with st.expander("Calculation", expanded=verdict == "CONTRADICTED"):
#         if not calculation:
#             _unavailable(_reason(calculation, raw, result))
#             return

#         inputs = _mapping(calculation.get("inputs"))
#         original = _raw_inputs(calculation, raw, result)
#         margin_analysis = "pp_change" in calculation or "relative_change" in calculation
#         formula = str(_first(calculation.get("formula"), result.get("formula"), ""))
#         spec = FORMULAS.get(re.sub(r"\s+", "", formula.lower()))
#         currency = _currency(calculation, raw, result)

#         _render_inputs(inputs, original, calculation, currency, spec, margin_analysis)

#         if margin_analysis:
#             _render_margin_analysis(calculation, inputs, comparison, parsed, result, currency)
#             _render_note(calculation)
#             return

#         if _decimal(calculation.get("value")) is None:
#             _unavailable(_reason(calculation, raw, result))
#             return

#         if formula:
#             st.markdown("#### Formula")
#             st.code(spec[3] if spec else formula, language=None)
#         if spec and all(_decimal(inputs.get(name)) is not None for name in spec[2]):
#             values = {name: _number(inputs[name]) for name in spec[2]}
#             st.markdown("#### Substitution using normalized values")
#             st.code(spec[4].format(**values), language=None)
#         elif formula:
#             st.caption("A substitution cannot be shown without all numerical inputs.")

#         unit = spec[1] if spec else _unit(_first(calculation.get("result_unit"), result.get("unit"), parsed.get("unit")))
#         if unit == "amount":
#             unit = _unit(calculation.get("result_unit")) or currency
#         claimed = _first(result.get("claimed_value"), result.get("claimed"), comparison.get("claimed"), parsed.get("claimed_change"))
#         actual = _first(result.get("actual_value"), result.get("actual"), comparison.get("actual"), calculation.get("value"))
#         _render_comparison(claimed, actual, unit, currency, calculation, comparison, result)
#         if spec:
#             st.caption(f"Calculation type: {spec[0]}")
#         _render_note(calculation)


# def _render_inputs(inputs, original, calculation, currency, spec, margin_analysis):
#     st.markdown("#### Input values")
#     keys = [key for key in dict.fromkeys([*inputs, *original]) if key not in {"unit", "currency", "precision"}]
#     if not keys:
#         st.caption("No input values were returned by the backend.")
#         return
#     metadata = _mapping(calculation.get("input_metadata"))
#     for key in keys:
#         value = inputs.get(key)
#         source = original.get(key)
#         details = {**_mapping(value), **_mapping(metadata.get(key))}
#         percent_input = margin_analysis or key in {"old_margin", "new_margin"} or _unit(details.get("kind")) == "percent"
#         percent_input = percent_input or _unit(details.get("unit")) == "percent"
#         percent_input = percent_input or (isinstance(source, str) and "%" in source)
#         if key == "years":
#             input_currency, input_unit = None, "years"
#         elif percent_input:
#             input_currency, input_unit = None, "percent"
#         else:
#             input_currency = _first(details.get("currency"), _mapping(source).get("currency"), currency)
#             input_unit = _unit(_first(details.get("unit"), calculation.get("input_unit"), inputs.get("unit")))
#             input_unit = input_unit or input_currency
#         left, right = st.columns(2)
#         label = str(key).replace("_", " ").title()
#         left.markdown(f"**{label} — original document value**")
#         left.text(_original(source) if source is not None else "Not retained by the current backend")
#         right.markdown(f"**{label} — normalized value**")
#         right.text(_format(value, input_unit, details.get("precision")))
#         st.caption(
#             f"Currency: {input_currency or 'Not supplied / not applicable'} · "
#             f"Input unit: {input_unit or 'Not supplied'}"
#         )


# def _render_margin_analysis(calculation, inputs, comparison, parsed, result, currency):
#     st.markdown("#### Margin change interpretations")
#     pp = calculation.get("pp_change")
#     relative = calculation.get("relative_change")
#     if _decimal(pp) is None and _decimal(relative) is None:
#         _unavailable(str(calculation.get("note") or "Neither margin calculation returned a finite value."))
#         return
#     left, right = st.columns(2)
#     precision = calculation.get("result_precision")
#     left.metric("Percentage-point change", _format(pp, "percentage points", precision))
#     right.metric("Relative percentage change", _format(relative, "percent", precision))
#     old, new = inputs.get("old"), inputs.get("new")
#     if _decimal(old) is not None and _decimal(new) is not None:
#         left.code(f"{_number(new)} − {_number(old)}", language=None)
#         if _decimal(old) != 0:
#             right.code(f"({_number(new)} − {_number(old)}) ÷ {_number(old)} × 100", language=None)
#         else:
#             right.caption("Relative change is undefined because the old margin is zero.")
#     intended = calculation.get("likely_intended")
#     if intended not in {"pp", "relative"}:
#         st.warning("The backend has not selected a margin interpretation. Both calculations are shown; no comparison is inferred.")
#         return
#     actual = pp if intended == "pp" else relative
#     unit = "percentage points" if intended == "pp" else "percent"
#     st.caption(f"Backend-selected interpretation: {unit}.")
#     if _decimal(actual) is None:
#         _unavailable("The selected margin interpretation is undefined.")
#         return
#     claimed = _first(result.get("claimed_value"), comparison.get("claimed"), parsed.get("claimed_change"))
#     _render_comparison(claimed, actual, unit, None, calculation, comparison, result)


# def _render_comparison(claimed, actual, unit, currency, calculation, comparison, result):
#     st.markdown("#### Result")
#     precision = calculation.get("result_precision")
#     claimed_precision = _first(result.get("claimed_precision"), comparison.get("claimed_precision"))
#     actual_precision = _first(result.get("actual_precision"), comparison.get("actual_precision"), precision)
#     difference_precision = _first(result.get("difference_precision"), comparison.get("difference_precision"), precision)
#     difference = _first(result.get("difference"), comparison.get("difference"))
#     if _decimal(difference) is None:
#         c, a = _decimal(claimed), _decimal(actual)
#         difference = c - a if c is not None and a is not None else None
#     difference_unit = "percentage points" if _unit(unit) in {"percent", "percentage points"} else unit
#     actual_col, claimed_col, difference_col = st.columns(3)
#     actual_col.metric("Actual", _format(actual, unit, actual_precision))
#     claimed_col.metric("Claimed", _format(claimed, unit, claimed_precision))
#     difference_col.metric("Signed difference", _format(difference, difference_unit, difference_precision, signed=True))
#     st.caption("Signed difference = claimed − actual. Positive means the claim is higher; negative means it is lower.")
#     st.caption(f"Result unit: {_unit(unit) or 'Not supplied'}")


# def _mapping(value: Any) -> dict[str, Any]:
#     return value if isinstance(value, dict) else {}


# def _first(*values: Any) -> Any:
#     return next((value for value in values if value is not None), None)


# def _decimal(value: Any) -> Decimal | None:
#     if isinstance(value, dict):
#         value = value.get("value")
#     if value is None or isinstance(value, bool):
#         return None
#     try:
#         number = Decimal(str(value).replace(",", "").strip())
#         return number if number.is_finite() else None
#     except (InvalidOperation, ValueError, TypeError):
#         return None


# def _number(value: Any, precision: Any = None, signed: bool = False) -> str:
#     number = _decimal(value)
#     if number is None:
#         return "—"
#     if number == 0:
#         number = abs(number)
#     if isinstance(precision, int) and not isinstance(precision, bool) and 0 <= precision <= 12:
#         with localcontext() as context:
#             context.prec = max(28, len(number.as_tuple().digits) + abs(number.adjusted()) + precision + 5)
#             number = number.quantize(Decimal(1).scaleb(-precision))
#         text = format(number, f",.{precision}f")
#     else:
#         text = format(number, ",f")
#         # Preserve explicit string/Decimal precision. Floats cannot retain
#         # the source's trailing zeros; show those in the original column.
#         original = value.get("value") if isinstance(value, dict) else value
#         if not isinstance(original, (str, Decimal)) and "." in text:
#             text = text.rstrip("0").rstrip(".")
#     if signed and number > 0:
#         text = "+" + text
#     return text.replace("-", "−")


# def _unit(value: Any) -> str | None:
#     if value is None:
#         return None
#     text = str(value).strip()
#     if not text:
#         return None
#     key = text.lower().replace("_", " ").replace("-", " ")
#     if key in {"%", "percent", "percentage"}:
#         return "percent"
#     if key in {"pp", "ppt", "point", "points", "percentage point", "percentage points"}:
#         return "percentage points"
#     return text


# def _format(value: Any, unit: Any, precision: Any = None, signed: bool = False) -> str:
#     text = _number(value, precision, signed)
#     if text == "—":
#         return text
#     unit = _unit(unit)
#     if unit == "percent":
#         return text + "%"
#     return f"{text} {unit}" if unit else text


# def _original(value: Any) -> str:
#     if isinstance(value, dict):
#         value = _first(value.get("display_value"), value.get("raw"), value.get("original"), value.get("text"), value.get("value"))
#     return str(value) if value is not None else "Not retained by the current backend"


# def _raw_inputs(calculation, raw, result):
#     for source in [calculation, raw, result]:
#         for key in ["raw_inputs", "source_values", "original_values"]:
#             candidate = _mapping(source.get(key))
#             if candidate:
#                 return candidate
#     old = _first(raw.get("old_raw"), result.get("old_raw"))
#     new = _first(raw.get("new_raw"), result.get("new_raw"))
#     return {"old": old, "new": new} if old is not None or new is not None else {}


# def _currency(*sources):
#     return _first(*(source.get("currency") for source in sources))


# def _reason(calculation, raw, result):
#     note = calculation.get("note")
#     if note:
#         return str(note)
#     for source in [raw, result]:
#         for key in ["calculation_unavailable_reason", "error", "notes", "reason"]:
#             value = source.get(key)
#             if isinstance(value, list):
#                 value = " ".join(str(item) for item in value if item)
#             if value:
#                 return str(value)
#     return "No finite deterministic calculation was returned by the backend."


# def _unavailable(reason):
#     st.warning("Calculation unavailable")
#     st.caption(reason)


# def _render_note(calculation):
#     if calculation.get("note"):
#         st.info(str(calculation["note"]))

# # from __future__ import annotations

# # from typing import Any

# # import streamlit as st


# # def render_calculation(
# #     result: dict[str, Any],
# # ) -> None:
# #     """
# #     Display an auditable deterministic calculation.

# #     Supports the dictionaries returned by financial_calculations.py:
# #     - percentage_growth
# #     - absolute_change
# #     - margin
# #     - CAGR
# #     - percentage-point change
# #     - analyze_change
# #     """

# #     calculation = result.get("calculation") or {}
# #     raw_result = result.get("raw") or {}

# #     expanded = result.get("verdict") == "CONTRADICTED"

# #     with st.expander(
# #         "Calculation",
# #         expanded=expanded,
# #     ):
# #         if not calculation:
# #             render_unavailable(
# #                 find_unavailable_reason(raw_result)
# #             )
# #             return

# #         if is_margin_analysis(calculation):
# #             render_margin_analysis(
# #                 calculation=calculation,
# #                 result=result,
# #             )
# #             return

# #         inputs = calculation.get("inputs") or {}
# #         raw_inputs = find_raw_inputs(
# #             calculation=calculation,
# #             raw_result=raw_result,
# #         )

# #         render_value_inputs(
# #             normalized_inputs=inputs,
# #             raw_inputs=raw_inputs,
# #             result=result,
# #         )

# #         calculated_value = calculation.get("value")
# #         formula = calculation.get("formula")

# #         if calculated_value is None:
# #             render_unavailable(
# #                 calculation.get("note")
# #                 or "The calculation returned no numerical result."
# #             )
# #             return

# #         if formula:
# #             st.markdown("#### Formula")
# #             st.code(
# #                 display_formula(formula),
# #                 language=None,
# #             )

# #             substituted = substitute_formula(
# #                 formula=formula,
# #                 inputs=inputs,
# #             )

# #             if substituted:
# #                 st.markdown("#### Substitution")
# #                 st.code(
# #                     substituted,
# #                     language=None,
# #                 )

# #         render_calculation_comparison(
# #             result=result,
# #             calculated_value=calculated_value,
# #             calculation=calculation,
# #         )

# #         if calculation.get("note"):
# #             st.info(str(calculation["note"]))


# # def render_value_inputs(
# #     normalized_inputs: dict[str, Any],
# #     raw_inputs: dict[str, Any],
# #     result: dict[str, Any],
# # ) -> None:
# #     st.markdown("#### Input values")

# #     input_names = list(
# #         dict.fromkeys(
# #             [
# #                 *raw_inputs.keys(),
# #                 *normalized_inputs.keys(),
# #             ]
# #         )
# #     )

# #     if not input_names:
# #         st.caption("No calculation inputs were returned.")
# #         return

# #     currency = find_currency(
# #         result=result,
# #         raw_inputs=raw_inputs,
# #     )
# #     unit = find_unit(result)

# #     for input_name in input_names:
# #         original_value = raw_inputs.get(input_name)
# #         normalized_value = normalized_inputs.get(input_name)

# #         col1, col2 = st.columns(2)

# #         col1.markdown(
# #             f"**{humanize(input_name)} — original**"
# #         )
# #         col1.write(
# #             preserve_precision(original_value)
# #             if original_value is not None
# #             else "Not retained by the current backend"
# #         )

# #         col2.markdown(
# #             f"**{humanize(input_name)} — normalized**"
# #         )
# #         col2.write(
# #             format_normalized_value(
# #                 normalized_value,
# #                 currency=currency,
# #                 unit=unit,
# #             )
# #         )

# #     metadata = []

# #     if currency:
# #         metadata.append(f"Currency: **{currency}**")

# #     if unit:
# #         metadata.append(f"Unit: **{unit}**")

# #     if metadata:
# #         st.caption(" · ".join(metadata))


# # def render_calculation_comparison(
# #     result: dict[str, Any],
# #     calculated_value: Any,
# #     calculation: dict[str, Any],
# # ) -> None:
# #     claimed = result.get("claimed_value")
# #     actual = first_not_none(
# #         result.get("actual_value"),
# #         calculated_value,
# #     )
# #     difference = result.get("difference")
# #     unit = find_unit(result)

# #     st.markdown("#### Result")

# #     actual_col, claimed_col, difference_col = st.columns(3)

# #     actual_col.metric(
# #         "Actual",
# #         format_result_value(actual, unit),
# #     )

# #     claimed_col.metric(
# #         "Claimed",
# #         format_result_value(claimed, unit),
# #     )

# #     difference_col.metric(
# #         "Signed difference",
# #         format_signed_difference(
# #             difference,
# #             unit,
# #         ),
# #     )

# #     formula_name = identify_calculation(
# #         calculation.get("formula")
# #     )

# #     if formula_name:
# #         st.caption(f"Calculation type: {formula_name}")


# # def render_margin_analysis(
# #     calculation: dict[str, Any],
# #     result: dict[str, Any],
# # ) -> None:
# #     """
# #     Handle analyze_change(), which returns both percentage-point
# #     and relative-percentage interpretations.
# #     """

# #     inputs = calculation.get("inputs") or {}
# #     old_value = inputs.get("old")
# #     new_value = inputs.get("new")

# #     st.markdown("#### Margin values")

# #     old_col, new_col = st.columns(2)

# #     old_col.metric(
# #         "Old margin",
# #         format_result_value(old_value, "percent"),
# #     )
# #     new_col.metric(
# #         "New margin",
# #         format_result_value(new_value, "percent"),
# #     )

# #     st.markdown("#### Two valid interpretations")

# #     pp_change = calculation.get("pp_change")
# #     relative_change = calculation.get("relative_change")
# #     intended = calculation.get("likely_intended")

# #     pp_col, relative_col = st.columns(2)

# #     pp_col.metric(
# #         "Percentage-point change",
# #         format_result_value(
# #             pp_change,
# #             "percentage points",
# #         ),
# #     )
# #     pp_col.code(
# #         f"{preserve_precision(new_value)} - "
# #         f"{preserve_precision(old_value)}",
# #         language=None,
# #     )

# #     relative_col.metric(
# #         "Relative percentage change",
# #         format_result_value(
# #             relative_change,
# #             "percent",
# #         ),
# #     )
# #     relative_col.code(
# #         (
# #             f"({preserve_precision(new_value)} - "
# #             f"{preserve_precision(old_value)}) / "
# #             f"{preserve_precision(old_value)} × 100"
# #         ),
# #         language=None,
# #     )

# #     if intended == "pp":
# #         st.success(
# #             "The claim wording indicates a percentage-point change."
# #         )
# #     elif intended == "relative":
# #         st.success(
# #             "The claim wording indicates a relative percentage change."
# #         )
# #     else:
# #         st.warning(
# #             "The claim wording does not clearly distinguish percentage "
# #             "change from percentage-point change."
# #         )

# #     claimed = result.get("claimed_value")
# #     difference = result.get("difference")

# #     comparison_col1, comparison_col2 = st.columns(2)

# #     comparison_col1.metric(
# #         "Claimed",
# #         format_result_value(
# #             claimed,
# #             find_unit(result),
# #         ),
# #     )

# #     comparison_col2.metric(
# #         "Signed difference",
# #         format_signed_difference(
# #             difference,
# #             find_unit(result),
# #         ),
# #     )

# #     if calculation.get("note"):
# #         st.info(str(calculation["note"]))


# # def render_unavailable(reason: str) -> None:
# #     st.warning("Calculation unavailable")
# #     st.caption(
# #         reason
# #         or "No deterministic calculation was returned."
# #     )


# # def find_raw_inputs(
# #     calculation: dict[str, Any],
# #     raw_result: dict[str, Any],
# # ) -> dict[str, Any]:
# #     """
# #     Look for original document strings without inventing them.

# #     Recommended future pipeline field:

# #         calculation["raw_inputs"] = {
# #             "old": "$10B",
# #             "new": "$11B"
# #         }
# #     """

# #     candidates = [
# #         calculation.get("raw_inputs"),
# #         raw_result.get("raw_inputs"),
# #         raw_result.get("source_values"),
# #         raw_result.get("original_values"),
# #     ]

# #     for candidate in candidates:
# #         if isinstance(candidate, dict) and candidate:
# #             return candidate

# #     old_raw = raw_result.get("old_raw")
# #     new_raw = raw_result.get("new_raw")

# #     if old_raw is not None or new_raw is not None:
# #         return {
# #             "old": old_raw,
# #             "new": new_raw,
# #         }

# #     return {}


# # def find_currency(
# #     result: dict[str, Any],
# #     raw_inputs: dict[str, Any],
# # ) -> str | None:
# #     raw_result = result.get("raw") or {}
# #     calculation = result.get("calculation") or {}

# #     currency = first_not_none(
# #         result.get("currency"),
# #         raw_result.get("currency"),
# #         calculation.get("currency"),
# #     )

# #     if currency:
# #         return str(currency)

# #     for value in raw_inputs.values():
# #         if isinstance(value, dict) and value.get("currency"):
# #             return str(value["currency"])

# #     return None


# # def find_unit(result: dict[str, Any]) -> str | None:
# #     raw_result = result.get("raw") or {}
# #     parsed_claim = raw_result.get("parsed_claim") or {}

# #     unit = first_not_none(
# #         result.get("unit"),
# #         raw_result.get("unit"),
# #         parsed_claim.get("unit"),
# #     )

# #     return str(unit) if unit else None


# # def find_unavailable_reason(
# #     raw_result: dict[str, Any],
# # ) -> str:
# #     notes = raw_result.get("notes")

# #     if isinstance(notes, list) and notes:
# #         return " ".join(
# #             str(note)
# #             for note in notes
# #             if note
# #         )

# #     if isinstance(notes, str) and notes:
# #         return notes

# #     return (
# #         raw_result.get("reason")
# #         or raw_result.get("error")
# #         or "No deterministic calculation was returned."
# #     )


# # def display_formula(formula: str) -> str:
# #     replacements = {
# #         "(new - old)/old*100": (
# #             "(New value − Old value) ÷ Old value × 100"
# #         ),
# #         "new - old": "New value − Old value",
# #         "profit / revenue * 100": (
# #             "Profit ÷ Revenue × 100"
# #         ),
# #         "(ending/beginning)^(1/years) - 1": (
# #             "((Ending ÷ Beginning)^(1 ÷ Years) − 1) × 100"
# #         ),
# #         "new_margin - old_margin": (
# #             "New margin − Old margin"
# #         ),
# #     }

# #     return replacements.get(formula, formula)


# # def substitute_formula(
# #     formula: str,
# #     inputs: dict[str, Any],
# # ) -> str | None:
# #     if not inputs:
# #         return None

# #     if formula == "(new - old)/old*100":
# #         old = preserve_precision(inputs.get("old"))
# #         new = preserve_precision(inputs.get("new"))
# #         return f"({new} − {old}) ÷ {old} × 100"

# #     if formula == "new - old":
# #         old = preserve_precision(inputs.get("old"))
# #         new = preserve_precision(inputs.get("new"))
# #         return f"{new} − {old}"

# #     if formula == "profit / revenue * 100":
# #         profit = preserve_precision(inputs.get("profit"))
# #         revenue = preserve_precision(inputs.get("revenue"))
# #         return f"{profit} ÷ {revenue} × 100"

# #     if formula == "(ending/beginning)^(1/years) - 1":
# #         beginning = preserve_precision(
# #             inputs.get("beginning")
# #         )
# #         ending = preserve_precision(
# #             inputs.get("ending")
# #         )
# #         years = preserve_precision(
# #             inputs.get("years")
# #         )

# #         return (
# #             f"(({ending} ÷ {beginning})"
# #             f"^(1 ÷ {years}) − 1) × 100"
# #         )

# #     if formula == "new_margin - old_margin":
# #         old_margin = preserve_precision(
# #             inputs.get("old_margin")
# #         )
# #         new_margin = preserve_precision(
# #             inputs.get("new_margin")
# #         )
# #         return f"{new_margin} − {old_margin}"

# #     return None


# # def identify_calculation(
# #     formula: str | None,
# # ) -> str | None:
# #     names = {
# #         "(new - old)/old*100": "Percentage growth",
# #         "new - old": "Absolute change",
# #         "profit / revenue * 100": "Margin",
# #         "(ending/beginning)^(1/years) - 1": "CAGR",
# #         "new_margin - old_margin": (
# #             "Percentage-point change"
# #         ),
# #     }

# #     return names.get(formula)


# # def is_margin_analysis(
# #     calculation: dict[str, Any],
# # ) -> bool:
# #     return (
# #         "pp_change" in calculation
# #         or "relative_change" in calculation
# #     )


# # def format_normalized_value(
# #     value: Any,
# #     currency: str | None,
# #     unit: str | None,
# # ) -> str:
# #     if value is None:
# #         return "—"

# #     if isinstance(value, dict):
# #         currency = value.get("currency") or currency
# #         value = value.get("value")

# #     text = preserve_precision(value)

# #     if currency:
# #         return f"{text} {currency}"

# #     if unit:
# #         return f"{text} {unit}"

# #     return text


# # def format_result_value(
# #     value: Any,
# #     unit: str | None,
# # ) -> str:
# #     if value is None:
# #         return "—"

# #     text = preserve_precision(value)
# #     unit_lower = str(unit or "").lower()

# #     if unit_lower in {
# #         "percent",
# #         "percentage",
# #         "%",
# #     }:
# #         return f"{text}%"

# #     if unit_lower in {
# #         "percentage point",
# #         "percentage points",
# #         "pp",
# #         "ppt",
# #     }:
# #         return f"{text} percentage points"

# #     return text


# # def format_signed_difference(
# #     value: Any,
# #     unit: str | None,
# # ) -> str:
# #     if value is None:
# #         return "—"

# #     try:
# #         number = float(value)
# #     except (TypeError, ValueError):
# #         return str(value)

# #     sign = "+" if number > 0 else ""
# #     text = f"{sign}{preserve_precision(number)}"

# #     unit_lower = str(unit or "").lower()

# #     if unit_lower in {
# #         "percent",
# #         "percentage",
# #         "%",
# #         "percentage point",
# #         "percentage points",
# #         "pp",
# #         "ppt",
# #     }:
# #         return f"{text} percentage points"

# #     return text


# # def preserve_precision(value: Any) -> str:
# #     """
# #     Preserve the precision already present in document strings.

# #     Numbers calculated by financial_calculations.py have already been
# #     rounded there, so this function does not add unnecessary decimals.
# #     """

# #     if value is None:
# #         return "—"

# #     if isinstance(value, dict):
# #         value = value.get("value")

# #     if isinstance(value, str):
# #         return value

# #     if isinstance(value, int):
# #         return f"{value:,}"

# #     if isinstance(value, float):
# #         if value.is_integer():
# #             return f"{int(value):,}"

# #         return f"{value:,}".rstrip("0").rstrip(".")

# #     return str(value)


# # def humanize(value: str) -> str:
# #     return value.replace("_", " ").strip().title()


# # def first_not_none(*values: Any) -> Any:
# #     for value in values:
# #         if value is not None:
# #             return value

# #     return None