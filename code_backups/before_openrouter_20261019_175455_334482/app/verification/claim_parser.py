from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from services.runtime import model_name, structured

DESC_MODEL = model_name()


class ParsedClaim(BaseModel):
    claim_text: str
    company: str | None = None
    subject: str | None = None
    metric: str | None = None
    segment: str | None = None
    start_period: int | None = None
    end_period: int | None = None
    period_type: Literal["annual", "quarter", "six_months", "nine_months", "point_in_time", "unspecified"] = "unspecified"
    temporal_status: Literal["historical", "forward_looking", "unspecified"] = "unspecified"
    quarter: int | None = Field(default=None, ge=1, le=4)
    operation: Literal["growth", "absolute_change", "percentage_points", "margin", "cagr", "level", "qualitative"] = "qualitative"
    claimed_literal: str | None = None
    claimed_change: float | None = None
    comparison_operator: Literal["equal", "greater_than", "at_least", "less_than", "at_most"] = "equal"
    direction: Literal["up", "down", "flat", "unspecified"] = "unspecified"
    unit: Literal["percent", "percentage_points", "amount", "number", "unspecified"] = "unspecified"
    claim_type: Literal["assertion", "question", "unclear"] = "assertion"
    notes: list[str] = Field(default_factory=list)


class ParsedClaims(BaseModel):
    claims: list[ParsedClaim]


def parse_claims(text: str) -> list[dict]:
    try:
        result = structured(ParsedClaims, text, system="""
Parse financial assertions into atomic claims. Preserve all factual clauses,
including period, company, segment, unit, qualifiers and numerical literals.
Do not split metric names such as research and development.
Copy claimed_literal exactly from the user's text, including %, currency and
unit scale. A value reached is level; a profit/revenue ratio is margin;
percentage points differs from relative percentage growth. Qualitative is
for assertions with no numeric threshold. Do not infer missing companies.
For explicit year-over-year growth in year Y, start may be Y-1; note inference.
Without an explicit quarter/month/YTD marker, growth 'in Y' refers to annual
figures rather than permission to choose quarterly data. Retain an explicitly
stated fiscal quarter number (Q1/first quarter, etc.) in quarter; do not infer it
from calendar months without the document fiscal calendar. Missing periods in
other assertions remain unspecified. Never invent a stated numeric amount.
Retain whether the assertion is historical or forward_looking (will, forecasts,
expects, next year). Do not resolve next year using the current calendar date;
its document-relative period needs source evidence. Do not discard negation
or approximately/at least/at most qualifiers. Retain explicit inequality wording
in comparison_operator; use equal only for equality or rounded/approximate assertions. If the claim is a question or
unclear assertion, retain that claim_type.
""")
        if not result.claims:
            raise ValueError("No atomic claims returned.")
        output = []
        for item in result.claims:
            if item.claimed_literal and item.claimed_literal not in text:
                raise ValueError("Parser returned a numeric literal absent from the claim.")
            pc = item.model_dump(mode="json")
            if (pc["operation"] == "growth" and pc["period_type"] == "annual"
                    and pc.get("end_period") and pc.get("start_period") is None):
                pc["start_period"] = pc["end_period"] - 1
                pc["notes"].append("Annual growth in a stated year is interpreted as a comparison with the preceding year.")
            pc["change_type"] = "decline" if item.direction == "down" else "growth"
            output.append(pc)
        return output
    except Exception as exc:
        # Do not manufacture a numeric structure after a provider failure.
        return [{"claim_text": text, "claim_type": "unclear", "operation": "qualitative",
                 "parsing_error": str(exc), "notes": [f"Claim parsing unavailable: {type(exc).__name__}: {exc}"]}]


def parse_claim(text: str) -> dict:
    return parse_claims(text)[0]

# import os
# import json
# import hashlib

# from google import genai

# CACHE_DIR = "data/claim_cache"
# DESC_MODEL = "gemini-flash-latest"
# CONFIDENCE_CUTOFF = 0.5

# FIELDS = ["subject", "metric","segment", "start_period", "end_period", "claimed_change", "change_type", "unit", "direction"]

# ESCALATION_CUTOFF = 0.6   # avg confidence below this -> re-parse weak claims


# def parse_claim(claim: str) -> dict:
#     # claim = claim.strip()

#     # cached = _cache_get(claim)
#     # if cached is not None:
#     #     return cached
#     # try:
#     #     result = _parse_with_llm(claim)
#     # except Exception as e:
#     #     # print(f"LLM parse failed {e} using rule based fallback")
#     #     # result = _parse_with_rules(claim)
#     #         # except Exception as e:
#     #     print(f"  LLM parse failed ({type(e).__name__}); using rule-based fallback")
#     #     result = _parse_with_rules(claim)

#     # result["confirmation_questions"] = _confirmation_questions(result)
#     # _cache_put(claim, result)
#     # return result
# # def parse_claim(claim: str) -> dict:
#     claim = claim.strip()

#     cached = _cache_get(claim)
#     if cached is not None:
#         return cached

#     from_llm = False
#     try:
#         result = _parse_with_llm(claim)
#         from_llm = True
#     except Exception as e:
#         print(f"  LLM parse failed ({type(e).__name__}); using rule-based fallback")
#         result = _parse_with_rules(claim)

#     result["confirmation_questions"] = _confirmation_questions(result)

#     # only cache good LLM results — never cache a fallback, so it retries next time
#     if from_llm:
#         _cache_put(claim, result)

#     return result
# def _parse_with_llm(claim):
#     client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
#     prompt = (
#         "Extract structured data from this financial claim. "
#         "Return ONLY valid JSON, no markdown, no commentary.\n\n"
#         f'Claim: "{claim}"\n\n'
#         "Fields:\n"
#         "- subject: the full thing being described (e.g. 'international revenue')\n"
#         "- metric: the measure (e.g. 'revenue', 'net income')\n"
#         "- segment: the sub-group if any (e.g. 'international'), else null\n"
#         "- start_period: the earlier year (INFER if implied, e.g. 'in 2025' implies 2024)\n"
#         "- end_period: the later year\n"
#         "- claimed_change: the numeric change value, number only\n"
#         "- change_type: 'growth' or 'decline' or 'no_change'\n"
#         "- unit: 'percent', 'dollars', or 'absolute'\n"
#         "- direction: 'up' or 'down' or 'flat'\n"
#         "- confidence: an object mapping EACH field above to a 0-1 confidence\n"
#         "- inferred: a list of field names you inferred rather than read directly\n"
#         "- claim_type: 'assertion' if the input asserts a specific verifiable fact "
#         "(e.g. 'revenue grew 40%'), 'question' if it asks for information "
#         "(e.g. 'what were the assets?'), or 'unclear'\n"
#     )
#     resp = client.models.generate_content(model = DESC_MODEL, contents = prompt)
#     raw = (resp.text or "").strip()
#     data = _extract_json(raw)
#     data["_source"] = "llm"
#     return data

# def _extract_json(text):
#     t = text.strip()
#     if t.startswith("'''"):
#         lines = [ln for ln in t.splitlines() if not ln.strip().startswith("'''")]
#         t = "\n".join(lines).strip()
#     return json.loads(t)

# def _parse_with_rules(claim):
#     lower = claim.lower()
#     tokens = lower.replace("%", " % ").replace("$", " $ ").split()
#     claimed_change = None
#     unit = "absolute"
#     for i, tok in enumerate(tokens):
#         num = _to_number(tok)
#         if num is not None:
#             nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
#             if nxt == "%" or "percent" in nxt:
#                 claimed_change, unit = num, "percent"
#                 break
#             if tok == "$" or "$" in tokens[max(0, i - 1)]:
#                 claimed_change, unit = num, "dollars"
#                 break
#             if claimed_change is None:
#                 claimed_change = num

#     clean = [t.strip(".,;:()%$") for t in tokens]
#     years = sorted({int(t) for t in clean if t.isdigit() and len(t) ==4 and 1900 < int(t) <2100})
#     end_period = years[-1] if years else None
#     start_period = years[0] if len(years) > 1 else (end_period - 1 if end_period else None)


#     growth_words = ("grew", "rose", "increased", "up", "gained", "growth")
#     decline_words = ("fell", "dropped", "declined", "down", "lost", "decline")

#     if any(w in lower  for w in growth_words):
#         change_type, direction = "growth", "up"
#     elif any(w in lower for w in decline_words):
#         change_type, direction = "decline", "down"
#     else:
#         change_type, direction = "no_change", "flat"

#     segment = None
#     for seg in ("international", "domestic", "north america", "europe",
#                 "americas", "greater china", "japan"):
#         if seg in lower:
#             segment = seg
#             break

#     metric = "revenue" if "revenue" in lower else None
#     # subject = f"{segment} {metric}".strip() if (segment or metric) else claim
#     parts = [p for p in (segment, metric) if p]
#     subject = " ".join(parts) if parts else claim

#     confidence = {f: 0.4 for f in FIELDS}
#     inferred = []
#     if len(years) < 2 and start_period is not None:
#         inferred.append("start_period")
#     return {
#         "subject": subject or None,
#         "metric": metric,
#         "segment": segment,
#         "start_period": start_period,
#         "end_period": end_period,
#         "claimed_change": claimed_change,
#         "change_type": change_type,
#         "unit": unit,
#         "direction": direction,
#         "claim_type": "unclear",   # fallback can't reliably tell; mark unclear
#         "confidence": confidence,
#         "inferred": inferred,
#         "_source": "rules",
#     }

# def _to_number(tok):
#     tok = tok.replace(",", "")
#     try:
#         return float(tok) if "." in tok else int(tok)
#     except ValueError:
#         return None

# def _confirmation_questions(result):
#     if result.get("claim_type") != "assertion":
#         return []
#     questions = []
#     inferred = set(result.get("inferred") or [])
#     conf = result.get("confidence") or {}

#     # for field in inferred:
#     #     c = conf.get(field, 0)
#     #     if c < CONFIDENCE_CUTOFF:
#     #         questions.append({
#     #             "field" : field,
#     #             "assumed_value" : result.get(field),
#     #             "confidence" : c,
#     #             "question" : _phrase_question(field, result.get(field))
#     #         })
#     # return questions
#     for field in FIELDS:
#         c = conf.get(field, 0)
#         is_inferred = field in inferred
#         is_low = c < CONFIDENCE_CUTOFF
#         if is_inferred or is_low:
#             # if result.get(field) is None and c == 0.0:
#             #     continue
#             questions.append({
#                 "field": field,
#                 "assumed_value": result.get(field),
#                 "confidence": c,
#                 "question": _phrase_question(field, result.get(field)),
#             })
#     return questions

# def _phrase_question(field, value):
#     phrasings = {
#         "start_period": f"I assumed the comparison starts from {value}. Is that the right base year?",
#         "end_period": f"I read the end year as {value}. Correct?",
#         "segment": f"I took the segment to be '{value}'. Is that right?",
#         "metric": f"I read the metric as '{value}'. Correct?",
#         "claimed_change": f"I read the claimed change as {value}. Is that right?",
#         "unit": f"I assumed the change is measured in {value}. Correct?",
#     }
#     return phrasings.get(field, f"I assumed {field} = {value}. Is that correct?")

# def _cache_key(claim):
#     return hashlib.sha256(claim.lower().encode("utf-8")).hexdigest()[:16]

# def _cache_path(claim):
#     os.makedirs(CACHE_DIR, exist_ok= True)
#     return os.path.join(CACHE_DIR, _cache_key(claim) + ".json")

# def _cache_get(claim):
#     path = _cache_path(claim)
#     if os.path.exists(path):
#         with open(path, encoding = "utf-8") as f:
#             return json.load(f)
#     return None

# def _cache_put(claim, result):
#     with open(_cache_path(claim), "w", encoding="utf-8") as f:
#         json.dump(result, f, indent = 2, ensure_ascii=False)

# def parse_claims(text: str) -> list:
#     """
#     Always returns a LIST of parsed claim dicts, one per detected claim.
#     Tier 1: option 4 (single LLM call splits + parses all).
#     Tier 2: if avg confidence < cutoff, re-parse the weak claims via parse_claim.
#     Tier 3: if the LLM is down entirely, rule-based split + rule parse.
#     """
#     text = text.strip()

#     try:
#         claims = _split_and_parse_all(text)          # tier 1
#     except Exception as e:
#         print(f"  compound LLM parse failed ({type(e).__name__}); rule fallback")
#         return _rule_split_and_parse(text)           # tier 3

#     # tier 2: check average confidence, escalate weak ones only
#     avg = _avg_confidence(claims)
#     if avg < ESCALATION_CUTOFF:
#         print(f"  avg confidence {avg:.2f} < {ESCALATION_CUTOFF}; "
#               f"re-parsing weak claims individually")
#         claims = _escalate_weak_claims(claims)

#     return claims


# # ---------------- tier 1: one call, split + parse ----------------
# def _split_and_parse_all(text):
#     client = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])

#     prompt = (
#         "A user gave a statement that may contain MULTIPLE financial claims. "
#         "Split it into individual claims and extract structured data for each. "
#         "Return ONLY a JSON array (no markdown), one object per claim.\n\n"
#         f'Statement: "{text}"\n\n'
#         "Each object must have these fields:\n"
#         "- claim_text: the individual claim as its own sentence\n"
#         "- subject, metric, segment (segment null if none)\n"
#         "- start_period, end_period (INFER if implied; 'in 2025' implies 2024)\n"
#         "- claimed_change (number only), change_type ('growth'/'decline'/'no_change')\n"
#         "- unit ('percent'/'dollars'/'absolute'), direction ('up'/'down'/'flat')\n"
#         "- claim_type ('assertion'/'question'/'unclear')\n"
#         "- confidence: object mapping each field to 0-1\n"
#         "- inferred: list of field names you inferred\n\n"
#         "If there is only one claim, return an array with one object."
#     )

#     resp = client.models.generate_content(model=DESC_MODEL, contents=prompt)
#     raw = (resp.text or "").strip()
#     data = _extract_json_array(raw)

#     claims = []
#     for obj in data:
#         obj["_source"] = "llm_compound"
#         obj["confirmation_questions"] = _confirmation_questions(obj)
#         # cache each sub-claim individually by its own text
#         ct = (obj.get("claim_text") or "").strip()
#         if ct and obj.get("claim_type") == "assertion":
#             _cache_put(ct, obj)
#         claims.append(obj)
#     return claims


# def _extract_json_array(text):
#     """Like _extract_json but expects a top-level array."""
#     t = text.strip()
#     if t.startswith("```"):
#         lines = [ln for ln in t.splitlines() if not ln.strip().startswith("```")]
#         t = "\n".join(lines).strip()
#     data = json.loads(t)
#     # tolerate the model returning a single object instead of an array
#     return data if isinstance(data, list) else [data]


# # ---------------- tier 2: escalate weak claims ----------------
# def _escalate_weak_claims(claims):
#     upgraded = []
#     for c in claims:
#         if _claim_confidence(c) < ESCALATION_CUTOFF:
#             ct = (c.get("claim_text") or "").strip()
#             if ct:
#                 # parse_claim already does cache -> LLM -> rules, one claim at a time
#                 better = parse_claim(ct)
#                 better["claim_text"] = ct
#                 upgraded.append(better)
#                 continue
#         upgraded.append(c)
#     return upgraded


# # ---------------- tier 3: rule-based, safe split only ----------------
# def _rule_split_and_parse(text):
#     parts = _rule_split(text)
#     return [_with_claim_text(parse_claim(p), p) for p in parts if p.strip()]


# def _rule_split(text):
#     """Conservative split: only on ';' and standalone ' and '."""
#     # split on semicolons first
#     chunks = []
#     for semi in text.split(";"):
#         # then split each on ' and ' (surrounded by spaces to avoid 'brand', 'and'-in-word)
#         chunks.extend(semi.split(" and "))
#     # return [c.strip() for c in chunks if c.strip()]
#     return [c.strip().rstrip(".") for c in chunks if c.strip()]

# def _with_claim_text(parsed, text):
#     parsed["claim_text"] = text
#     return parsed


# # ---------------- confidence helpers ----------------
# def _claim_confidence(claim):
#     """Average of a single claim's per-field confidences."""
#     conf = claim.get("confidence") or {}
#     vals = [conf.get(f, 0) for f in FIELDS]
#     return sum(vals) / len(vals) if vals else 0


# def _avg_confidence(claims):
#     if not claims:
#         return 0
#     return sum(_claim_confidence(c) for c in claims) / len(claims)


# if __name__ == "__main__":
#     import sys
#     text = sys.argv[1] if len(sys.argv) > 1 else \
#         "Revenue grew 25% and operating margin reached 30%."
#     claims = parse_claims(text)
#     print(f"detected {len(claims)} claim(s):\n")
#     for i, c in enumerate(claims, 1):
#         print(f"--- Claim {i}: {c.get('claim_text')} ---")
#         print(json.dumps(c, indent=2, ensure_ascii=False))
#         print()
# # if __name__ == "__main__":
# #     import sys
# #     claim = sys.argv[1] if len(sys.argv) > 1 else "International revenue grew by 40 % in 2025"
# #     result = parse_claim(claim)
# #     print(json.dumps(result, indent=2, ensure_ascii=False))
