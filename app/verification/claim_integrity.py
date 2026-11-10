"""Bind parsed quantities to original metric clauses; check whole-input coverage."""
from __future__ import annotations
import re
from services.verification_errors import ParseValidationError
from verification.number_parser import parse_number
from verification.reporting_periods import DATE, explicit_dates, explicit_period, explicit_quarters

COMPARATIVE={"growth","absolute_change","percentage_points","cagr","direction"}
UP=re.compile(r"\b(?:grow|grew|grown|growth|increase[ds]?|increasing|rose|rise[sn]?|rising|higher)\b",re.I)
DOWN=re.compile(r"\b(?:decline[ds]?|declining|decrease[ds]?|decreasing|fell|fall[sn]?|falling|lower|drop(?:ped|s|ping)?)\b",re.I)
FLAT=re.compile(r"\b(?:unchanged|flat|stable)\b|\b(?:no change|remained the same)\b",re.I)

QUANTITY=re.compile(r"(?<!\w)(?:(?:USD|EUR|GBP|CAD|AUD|JPY|CNY|CHF|INR|HKD)\s*)?[$€£]?\(?[+−-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?(?:\s*(?:%|percent\b|billions?\b|millions?\b|thousands?\b|trillions?\b|bn\b|[BMTK]\b))?(?!\w)",re.I)
STOP=set("a an the s of at as in on for from to by with between during ended ending fiscal year years months month quarter annual approximately about roughly around nearly only just respectively and or but while whereas than more less no least most is are was were has have had been it its that this those these company corporation inc ltd limited grew grow growth increased increase increasing decreased decrease decreasing declined decline declining fell fall rose rise change changed reported recorded amounted generated earned paid totaled totalled reached expects plans will would be consequently therefore hence so offset offsetting due because both over under above below since".split())
STOP.update("january february march april may june july august september october november december jan feb mar apr jun jul aug sep sept oct nov dec".split())
STOP.update("grow grown increases increasing decreases rising rises falls falling lower higher dropped drops dropping unchanged flat stable cagr".split())


def words(text):
    return re.findall(r"\w+",str(text or "").casefold())


def _spans(phrase,text):
    tokens=list(re.finditer(r"\w+",text.casefold()))
    needle=words(phrase)
    return [(tokens[i].start(),tokens[i+len(needle)-1].end()) for i in range(len(tokens)-len(needle)+1)
            if needle and [m.group() for m in tokens[i:i+len(needle)]]==needle]


def quantities(text,companies=()):
    excluded=[m.span() for m in DATE.finditer(text)]
    for company in companies:
        excluded.extend(_spans(company,text))
    result=[]
    for match in QUANTITY.finditer(text):
        raw=match.group().strip()
        if any(start<=match.start()<end for start,end in excluded): continue
        if re.fullmatch(r"(?:19|20)\d{2}",raw): continue
        if re.match(r"\s*(?:years?|months?|quarters?)\b",text[match.end():],re.I): continue
        if parse_number(raw)[0] is not None: result.append((match.start(),match.start()+len(raw),raw))
    return result


def _without_periods(text):
    text=DATE.sub(" ",text)
    return re.sub(r"\b(?:19|20)\d{2}\b"," ",text)


def _content(text):
    text=_without_periods(text)
    text=QUANTITY.sub(" ",text)
    return set(words(text))-STOP


def _bound(pc,text,spans,index):
    start,end,_=spans[index]
    left=spans[index-1][1] if index else 0
    right=spans[index+1][0] if index+1<len(spans) else len(text)
    metric=pc.get("metric") or pc.get("subject")
    allowed=STOP|set(words(pc.get("company")))
    before=_without_periods(text[left:start])
    matches=_spans(metric,before)
    if matches:
        # A new subject after this metric must not inherit its quantity.
        tail=before[matches[-1][1]:]
        if pc.get("operation")=="ratio" and re.search(r"\b(?:share|proportion|percentage|ratio)\b",tail,re.I):
            allowed|=set(words(pc.get("denominator_metric")))|{"share","proportion","percentage","ratio"}
        if not (set(words(tail))-allowed): return True
    after=text[end:right]
    matches=_spans(metric,after)
    if matches and re.fullmatch(r"\s*(?:(?:increase|decrease|decline|growth|change)\s+)?(?:in|of|for)\s+",after[:matches[0][0]],re.I):
        return True
    # Shared metric in "revenue was 100 in 2025 and 120 in 2026". Stop at
    # content-bearing new clauses such as "and profit", rather than borrowing it.
    if index and not (set(words(before))-allowed):
        return _bound(pc,text,spans,index-1)
    return False


def source_quantity_matches(pc,text,start,end):
    """Bind a proposed prose source address to its own metric, excluding dates."""
    spans=quantities(text)
    return any(start<=left and right<=end and _bound(pc,text,spans,index)
               for index,(left,right,_) in enumerate(spans))


def uniquely_bound_literal(pc,original,literal,atom):
    """Restore only an absent quantity, never conceal a conflicting display value."""
    companies=[pc["company"]] if pc.get("company") else ()
    # Ignore full dates and company names, but a bare 2026 can still be a
    # conflicting count. Do not discard it as a year when deciding to repair.
    display=DATE.sub(" ",atom)
    for company in companies:
        for start,end in reversed(_spans(company,display)):
            display=display[:start]+" "+display[end:]
    if QUANTITY.search(display):
        return False
    metric=next((value for value in (pc.get("metric"),pc.get("subject"))
                 if value and _spans(value,atom) and _spans(value,original)),None)
    if not metric:
        return False
    spans=quantities(original,companies)
    candidates=[index for index,(_,_,raw) in enumerate(spans) if raw==literal]
    bound_pc=dict(pc,metric=metric)
    return len([index for index in candidates if _bound(bound_pc,original,spans,index)])==1


def _sentence_bounds(text,start,end):
    boundaries=list(re.finditer(r"(?<=[.!?])\s+(?=[A-Z])|;\s*",text))
    left=max([m.end() for m in boundaries if m.end()<=start] or [0])
    right=min([m.start() for m in boundaries if m.start()>=end] or [len(text)])
    return left,right


def _clause(text,spans,index):
    """Return the assertion and the value's position inside it."""
    start,end,_=spans[index]
    left,right=_sentence_bounds(text,start,end)
    separator=r"\b(?:and|but|while|whereas|versus|from|to|as|(?:more\s+than\s+)?offset(?:ting)?)\b"
    if index and spans[index-1][1]>left:
        gap_start=spans[index-1][1]
        separators=list(re.finditer(separator,text[gap_start:start],re.I))
        if separators: left=gap_start+separators[-1].end()
    if index+1<len(spans) and spans[index+1][0]<right:
        gap_end=spans[index+1][0]
        separator_match=re.search(separator,text[end:gap_end],re.I)
        if separator_match: right=end+separator_match.start()
    return text[left:right],start-left


def _periods(text):
    """Retain complete dates, with year-only periods only where actually stated."""
    dates=explicit_dates(text)
    amounts=quantities(text)
    tokens=[(match.start(),day) for day,match in dates]
    for match in re.finditer(r"\b(?:19|20)\d{2}\b",text):
        if any(date.start()<=match.start()<date.end() for _,date in dates): continue
        if any(left<=match.start()<right for left,right,_ in amounts): continue
        tokens.append((match.start(),match.group()))
    return list(dict.fromkeys(value for _,value in sorted(tokens)))


def _shared_periods(text,spans):
    if not spans: return []
    if re.search(r"\b(?:next|last|previous|following)\s+year\b",text,re.I): return []
    prefix=text[:spans[0][0]]
    periods=_periods(prefix)
    if re.match(r"\s*(?:between|from)\b",prefix,re.I) and len(periods)==2:
        return periods if set(_periods(text))==set(periods) else []
    all_periods=_periods(text)
    if len(all_periods)==1:
        return all_periods
    suffix=text[spans[-1][1]:]
    if (len(all_periods)==2 and _periods(suffix)==all_periods
            and re.search(r"\b(?:between|from)\b",suffix,re.I)
            and _sentence_bounds(text,spans[0][0],spans[0][1])[1]>=spans[-1][1]):
        return all_periods
    return []


def _input_periods(text,spans,index,clause):
    left,right=_sentence_bounds(text,spans[index][0],spans[index][1])
    sentence=text[left:right]
    if re.search(r"\brespectively\b",sentence,re.I):
        local=[i for i,(start,end,_) in enumerate(spans) if left<=start and end<=right]
        periods=_periods(sentence)
        if len(periods)==len(local)>1:
            return [periods[local.index(index)]],True
        return [],False
    shared=_shared_periods(text,spans)
    # Explicit from/to values inherit the corresponding endpoints, rather than
    # borrowing the entire date range as a single-date assertion.
    if len(shared)==2:
        for first in (index-1,index):
            if first<0 or first+1>=len(spans): continue
            left=spans[first-1][1] if first else 0
            prefix=text[left:spans[first][0]]
            bridge=text[spans[first][1]:spans[first+1][0]]
            if (re.search(r"\bfrom\s+(?:(?:approximately|about|roughly|around)\s+)?$",prefix,re.I)
                    and re.fullmatch(r"\s+to\s+(?:(?:approximately|about|roughly|around)\s+)?",bridge,re.I)):
                return [shared[index-first]],True
    return _periods(clause) or shared,False


def comparison_operator(atom,literal,position=None):
    """Read a threshold at one quantity address, not another equal literal."""
    position=atom.index(literal) if position is None else position
    prefix,suffix=atom[:position],atom[position+len(literal):]
    prefix=re.sub(r"\b(?:approximately|about|roughly|around)\s*$","",prefix,flags=re.I).rstrip()
    patterns=(("at_least",r"(?:at least|no less than|not less than|>=|≥)\s*$"),
              ("at_most",r"(?:at most|no more than|not more than|<=|≤)\s*$"),
              ("greater_than",r"(?:more than|greater than|higher than|over|above|exceeds?|exceeded|(?<![><=])>)\s*$"),
              ("less_than",r"(?:less than|lower than|under|below|(?<![><=])<)\s*$"))
    for operator,pattern in patterns:
        if re.search(r"(?:\b|(?=[><≥≤]))"+pattern,prefix,re.I): return operator
    if re.match(r"\s+or (?:more|higher|greater)\b",suffix,re.I): return "at_least"
    if re.match(r"\s+or (?:less|lower|fewer)\b",suffix,re.I): return "at_most"
    return "equal"


def original_comparisons(pc,original):
    spans=quantities(original,[pc["company"]] if pc.get("company") else ())
    periods=set(_periods(pc["claim_text"]+" "+(pc.get("period_quote") or "")))
    operators=set()
    for index,(_,_,raw) in enumerate(spans):
        if raw!=pc.get("claimed_literal") or not _bound(pc,original,spans,index): continue
        clause,position=_clause(original,spans,index)
        source_periods,_=_input_periods(original,spans,index,clause)
        if set(source_periods)==periods:
            operators.add(comparison_operator(clause,raw,position))
    return operators


def _direction(pc,text):
    # Company/metric names can themselves contain words such as "growth".
    for phrase in (pc.get("metric"),pc.get("company"),pc.get("denominator_metric")):
        if phrase:
            for start,end in reversed(_spans(phrase,text)):
                text=text[:start]+" "+text[end:]
    directions={name for name,pattern in (("up",UP),("down",DOWN),("flat",FLAT)) if pattern.search(text)}
    if len(directions)>1:
        raise ParseValidationError("The original change direction is ambiguous; retain separate assertions.",reason="operation")
    return next(iter(directions),None)


def normalize_direction(pc):
    """Use the retained assertion's explicit wording/sign, not an LLM flag."""
    if pc.get("claim_type")=="assertion" and pc.get("operation")=="level" and _direction(pc,pc["claim_text"]):
        raise ParseValidationError("An explicit increase/decrease needs a change operation, not a level assertion.",reason="operation")
    if pc.get("claim_type")!="assertion" or pc.get("operation") not in COMPARATIVE:
        return
    atom=pc["claim_text"]
    if re.search(r"\b(?:not|never)\s+(?:(?:approximately|about|roughly)\s+)?(?:grow|increase|decrease|decline|rise|fall)\b",atom,re.I):
        if not pc.get("negated"):
            raise ParseValidationError("A negated change must retain its negation; numerical negation remains unsupported.",reason="operation")
    if pc.get("negated"): return
    spans=quantities(atom,[pc["company"]] if pc.get("company") else ())
    literal=pc.get("claimed_literal")
    indices=[i for i,(_,_,raw) in enumerate(spans) if raw==literal]
    clause=_clause(atom,spans,indices[0])[0] if len(indices)==1 else atom
    direction=_direction(pc,clause)
    value=parse_number(literal)[0] if literal else None
    signed=None
    if value and (value["value"]<0 or re.search(r"[+−-]\s*\d",literal)):
        signed="down" if value["value"]<0 else "up" if value["value"]>0 else "flat"
    if signed and direction and signed!=direction:
        raise ParseValidationError("The explicit numeric sign conflicts with the stated change direction.",reason="operation")
    if not direction and value and pc.get("operation")=="cagr":
        signed="down" if value["value"]<0 else "up" if value["value"]>0 else "flat"
    direction=direction or signed
    if not direction:
        raise ParseValidationError("The change direction cannot be read unambiguously from the submitted assertion.",reason="operation")
    if pc.get("direction")!=direction:
        pc["direction"]=direction
        pc["notes"].append("Change direction normalized from original wording/sign.")


def _same_assertion(pc,original,spans,index):
    clause,position=_clause(original,spans,index)
    source_periods,endpoint=_input_periods(original,spans,index,clause)
    parsed_periods=_periods(pc["claim_text"]+" "+(pc.get("period_quote") or ""))
    if set(source_periods)!=set(parsed_periods): return False
    if comparison_operator(clause,spans[index][2],position)!=pc.get("comparison_operator","equal"): return False
    parent_left,parent_right=_sentence_bounds(original,spans[index][0],spans[index][1])
    semantic_context=original[parent_left:parent_right] if endpoint else clause
    if pc.get("operation") in {"ratio","margin"}:
        if not re.search(r"\b(?:share|proportion|percentage|percent|ratio|margin)\b|/",semantic_context,re.I): return False
    if pc.get("operation") not in {"ratio","margin"} and not (endpoint and pc.get("operation")=="level"):
        atom_spans=quantities(pc["claim_text"],[pc["company"]] if pc.get("company") else ())
        selected=[i for i,(_,_,raw) in enumerate(atom_spans) if raw==pc.get("claimed_literal")]
        atom_clause=_clause(pc["claim_text"],atom_spans,selected[0])[0] if len(selected)==1 else pc["claim_text"]
        if _direction(pc,clause)!=_direction(pc,atom_clause): return False
    try:
        period=explicit_period(clause) or (explicit_period(semantic_context) if endpoint else None)
    except ValueError:
        return False
    if period and pc.get("period_type") not in {period[0],"unspecified",None}: return False
    quarters=explicit_quarters(semantic_context)
    if not quarters and period is None and _shared_periods(original,spans):
        all_quarters=explicit_quarters(original)
        if len(all_quarters)==1 and explicit_quarters(original[:spans[0][0]])==all_quarters:
            quarters=all_quarters
    if quarters!=explicit_quarters(pc["claim_text"]+" "+(pc.get("period_quote") or "")): return False
    return True


def _scope_words(pc,text):
    """Separate distinct remaining scopes when equal quantities otherwise match."""
    for field in ("company","metric","subject","segment","denominator_metric"):
        if pc.get(field):
            for start,end in reversed(_spans(pc[field],text)):
                text=text[:start]+" "+text[end:]
    return frozenset(_content(text))


def validate_claims(parsed,original):
    parsed=[pc for pc in parsed if pc.get("claim_type")=="assertion"]
    if not parsed: return
    companies={pc["company"] for pc in parsed if pc.get("company")}
    spans=quantities(original,companies)
    covered=set()
    for atom_index,pc in enumerate(parsed,1):
        literal=pc.get("claimed_literal")
        if not literal: continue
        candidates=[i for i,(start,end,raw) in enumerate(spans) if literal==raw]
        # A numeric literal without its percentage-points suffix is valid.
        if not candidates:
            candidates=[i for i,(_,_,raw) in enumerate(spans) if parse_number(raw)[0]==parse_number(literal)[0]]
        bound=[i for i in candidates if _bound(pc,original,spans,i)]
        if not bound:
            raise ParseValidationError("A quantity moved away from its original metric/clause.",reason="binding")
        matched=[i for i in bound if _same_assertion(pc,original,spans,i)]
        scopes={_scope_words(pc,_clause(original,spans,i)[0]) for i in matched}
        if len(scopes)>1:
            atom_scope=_scope_words(pc,pc["claim_text"])
            matched=[i for i in matched if _scope_words(pc,_clause(original,spans,i)[0])==atom_scope]
        if not matched:
            error=ParseValidationError("A parsed assertion changed its original date, direction or reporting qualifier.",reason="binding")
            error.metadata["atom_index"]=atom_index
            raise error
        covered.update(matched)
    if set(range(len(spans)))-covered:
        raise ParseValidationError("Some original quantities were omitted from atomic claims.",reason="coverage")
    retained=" ".join(str(pc.get("claim_text") or "") for pc in parsed)
    if _content(original)-_content(retained):
        raise ParseValidationError("A factual clause or qualifier disappeared from the original input.",reason="coverage")
