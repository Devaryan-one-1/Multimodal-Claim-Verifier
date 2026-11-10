"""Repair parser date formatting and shared periods using explicit input only."""
from __future__ import annotations

import copy
import re

from services.verification_errors import ParseValidationError
from verification.claim_integrity import uniquely_bound_literal
from verification.number_parser import parse_number
from verification.reporting_periods import DATE, explicit_dates, explicit_period

COMPARATIVE={"growth","absolute_change","percentage_points","cagr","direction"}


def _phrase(value):
    return r"\b"+r"\W+".join(re.escape(word) for word in re.findall(r"\w+",value))+r"\b"


def _quote(quote,original,dates,shared):
    if not quote or quote in original:
        return quote
    quoted=explicit_dates(quote)
    remainder=DATE.sub(" ",quote)
    # Formatting/join words can change, but annual/quarter/YTD qualifiers cannot.
    connectors={"between","and","from","to","as","of","at","on"}
    if not quoted and shared:
        years=re.findall(r"\b(?:19|20)\d{2}\b",quote)
        remaining=re.sub(r"\b(?:19|20)\d{2}\b"," ",quote)
        if (years==[day[:4] for day,_ in dates] and
                not set(re.findall(r"\w+",remaining.casefold()))-connectors):
            return shared[1]
    if not quoted or set(re.findall(r"\w+",remainder.casefold()))-connectors:
        return quote
    wanted=[day for day,_ in quoted]
    for index in range(len(dates)-len(wanted)+1):
        chosen=dates[index:index+len(wanted)]
        if [day for day,_ in chosen]==wanted:
            return original[chosen[0][1].start():chosen[-1][1].end()]
    return quote


def _range(original,dates,years):
    if len(dates)!=2 or dates[0][0]>=dates[1][0]:
        return None
    (_,first),(_,second)=dates
    if not re.fullmatch(r"\s*(?:between|from)\s+",original[:first.start()],re.I):
        return None
    if not re.fullmatch(r"\s+(?:and|to)\s+",original[first.end():second.start()],re.I):
        return None
    if len(set(years))!=2 or set(years)!={int(day[:4]) for day,_ in dates}:
        return None
    if re.search(r"\b(?:next|last|previous|following)\s+year\b",original,re.I):
        return None
    try:
        period=explicit_period(original)
    except ValueError:
        # Different clauses can legitimately use different durations. They
        # cannot share an inferred range; each normalized atom checks its own.
        return None
    if period and period[0]!="point_in_time":
        return None
    boundary=re.search(r"[.!?]\s+(?=[A-Z])",original[second.end():])
    scope_end=second.end()+boundary.start() if boundary else len(original)
    return dates,original[first.start():second.end()],scope_end


def _ratio_atoms(pc,original,shared):
    if (pc.get("operation")!="ratio" or not shared or not pc.get("metric") or not pc.get("denominator_metric")
            or pc.get("temporal_status")=="forward_looking"):
        return None
    dates,_,_=shared
    percentage=r"\d+(?:\.\d+)?\s*(?:%|percent\b)"
    approximate=r"(?:(?:approximately|about|roughly|around)\s+)?"
    pattern=(rf"(?P<phrase>{_phrase(pc['metric'])}(?:[\u2019']s?)?\s+"
             rf"(?:share|proportion|percentage|ratio)\s+(?:of|to)\s+{_phrase(pc['denominator_metric'])})"
             rf"\s+(?P<verb>fell|declined|decreased|rose|increased|grew|changed|was|were)\s+from\s+"
             rf"(?P<approx1>{approximate})(?P<first>{percentage})\s+to\s+"
             rf"(?P<approx2>{approximate})(?P<second>{percentage})")
    matches=list(re.finditer(pattern,original,re.I))
    if len(matches)!=1:
        return None
    match=matches[0]
    first,second=match.group("first","second")
    if pc.get("claimed_literal") not in {first,second}:
        return None
    values=[parse_number(value)[0]["value"] for value in (first,second)]
    verb=match["verb"].casefold()
    if ((verb in {"fell","declined","decreased"} and values[1]>=values[0]) or
            (verb in {"rose","increased","grew"} and values[1]<=values[0])):
        raise ParseValidationError("Ratio from/to values conflict with their stated direction.",reason="operation")
    # A combined from/to atom expands to two facts. Already split atoms bind only
    # their own literal. Never calculate or invent another claimed quantity.
    combined=all(value in pc["claim_text"] for value in (first,second))
    company=""
    if pc.get("company"):
        name=re.search(_phrase(pc["company"])+r"(?:[\u2019']s)?",original,re.I)
        if name: company=name.group()+" "
    output=[]
    for index,((day,date),literal) in enumerate(zip(dates,(first,second))):
        if not combined and literal!=pc["claimed_literal"]:
            continue
        atom=copy.deepcopy(pc)
        atom.update(claim_text=f"{date.group()}, {company}{match['phrase']} was {match[f'approx{index+1}']}{literal}.",
                    start_period=None,end_period=int(day[:4]),period_quote=date.group(),
                    period_type="point_in_time",claimed_literal=literal,direction="flat")
        output.append(atom)
    return output


def prepare_claim_periods(items,original,years):
    """Normalize untrusted parser shapes before strict semantic validation."""
    dates=explicit_dates(original)
    shared=_range(original,dates,years)
    originals={day:match.group() for day,match in dates}
    def canonical_date(match):
        parsed=explicit_dates(match.group())
        return originals.get(parsed[0][0],match.group()) if parsed else match.group()
    output=[]
    seen_ratios=set()
    for item in items:
        pc=copy.deepcopy(item)
        pc["period_quote"]=_quote(pc.get("period_quote"),original,dates,shared)
        if pc.get("period_quote") and pc["period_quote"] not in original:
            raise ParseValidationError("The period quote cannot be grounded in the submitted dates.",rule="quote_not_grounded")
        pc["claim_text"]=DATE.sub(canonical_date,pc["claim_text"])
        literal=pc.get("claimed_literal")
        if (literal and literal in original and literal not in pc["claim_text"]
                and uniquely_bound_literal(pc,original,literal,pc["claim_text"])):
            display=pc["claim_text"].rstrip(" .;:")
            if len(display)+len(literal)+4<=2400:
                pc["claim_text"]=f"{display} ({literal})."
        expanded=_ratio_atoms(pc,original,shared)
        if expanded:
            for atom in expanded:
                key=(atom["metric"],atom["denominator_metric"],atom["period_quote"],atom["claimed_literal"])
                if key not in seen_ratios:
                    output.append(atom); seen_ratios.add(key)
            continue
        if (shared and pc.get("operation") in COMPARATIVE and pc.get("temporal_status")!="forward_looking" and not pc.get("period_quote")
                and pc.get("claimed_literal") and pc["claimed_literal"] in original[:shared[2]]
                and not re.search(r"\b(?:19|20)\d{2}\b",pc["claim_text"])):
            pc["period_quote"]=shared[1]
        output.append(pc)
    if len(output)>8:
        raise ParseValidationError("The input requires more than eight atomic targets; submit smaller groups.",reason="coverage")
    return output
