from __future__ import annotations

import json
import io
import math
import hashlib
import os
import re
import time
from pathlib import Path

import pymupdf
import streamlit as st
from PIL import Image, ImageDraw
from pilot import store
from pilot.exports import exports
from pilot import model_connections, private_media
from pilot.responsive import navigation
from services.verification_status import execution_failed


def _plain_label(value):
    return re.sub(r"([\\`*_{}\[\]()<>#!|])", r"\\\1", str(value))


def _ensure_access(access):
    if (st.session_state.get("customer_identity") != access.principal or
            st.session_state.get("customer_workspace", access.workspace) != access.workspace or
            access.expires <= time.time()):
        st.rerun()


def _remember_draft(key):
    st.session_state["draft_"+key] = st.session_state[key]


def _clear_drafts(keys, extra=None):
    for key in keys:
        st.session_state.pop(key, None)
        st.session_state.pop("draft_"+key, None)
    if extra:
        st.session_state.pop(extra, None)


def draft_text(label, key, default="", *, multiline=False, **options):
    # Widget state is cleaned when navigating away; this session copy is retained.
    # Never use this helper for credentials.
    if key not in st.session_state:
        st.session_state[key] = st.session_state.get("draft_"+key, default)
    render = st.text_area if multiline else st.text_input
    return render(label, key=key, on_change=_remember_draft, args=(key,), **options)


@st.fragment
def review_composer(access, key, document_ids, connected):
    _ensure_access(access)
    if st.button("Clear draft", key=key+"clear_draft"):
        _clear_drafts([key+"review_name", key+"claim"])
        st.rerun()
    name = draft_text("Review name", key+"review_name", "Financial review", max_chars=160)
    claim = draft_text("Claims to check", key+"claim", multiline=True, height=140, max_chars=6000,
                       placeholder="Include the company, metric and reporting period. Compound claims are checked separately.")
    st.caption("Drafts are kept in this session and workspace after you leave a field. Click Check figures to submit.")
    if not connected:
        st.info("Connect your OpenRouter key in Settings before submitting a new review. Your draft can stay here.")
    if st.button("Check figures", key=key+"check_figures", type="primary", disabled=not connected):
        try:
            job = store.submit_review(access, name, claim, document_ids)
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))
        else:
            _remember_draft(key+"review_name")
            _remember_draft(key+"claim")
            st.session_state[key+"selected_review"] = job
            st.rerun()


@st.fragment
def decision_editor(access, job_id, index, key):
    _ensure_access(access)
    try:
        prior = store.decisions(access, job_id).get(index, {})
    except PermissionError:
        st.rerun()
    choice_key, note_key, base_key = key+"choice", key+"note", key+"base_updated"
    st.button("Discard draft changes", key=key+"discard", on_click=_clear_drafts,
              args=([choice_key, note_key], base_key))
    if base_key not in st.session_state:
        st.session_state[base_key] = prior.get("updated")
    if choice_key not in st.session_state:
        st.session_state[choice_key] = st.session_state.get("draft_"+choice_key, prior.get("decision", "unreviewed"))
    choice = st.selectbox("Review decision", ["unreviewed", "reviewed", "incorrect_evidence"], key=choice_key,
                          on_change=_remember_draft, args=(choice_key,))
    note = draft_text("Review note", note_key, prior.get("note", ""), multiline=True, max_chars=2000)
    stale = st.session_state[base_key] != prior.get("updated")
    if stale:
        st.warning("Another reviewer changed this decision. Copy any note you need, then discard draft changes to load the latest decision.")
    if st.button("Save decision", key=key+"save", disabled=stale):
        try:
            store.decide(access, job_id, index, choice, note, expected_updated=st.session_state[base_key])
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state.pop(base_key, None)
            st.rerun()


def retry_unavailable_reason(access, job):
    if job.get("kind", "verify") in {"process", "verify"} and job.get("principal") != access.principal:
        return "Only the request creator can retry work using their OpenRouter credits."
    if job["attempts"] >= 3:
        return "This request has reached the three-attempt limit. Submit a new request after resolving the issue."
    if not store.can_retry(access, job):
        return "This request is not available for retry."
    if job["stage"].startswith("Code changed."):
        return "Code changed since this request started. Submit a new review to use this version."
    if job.get("kind", "verify") in {"process", "verify"}:
        binding = json.loads(job["payload"]).get("openrouter") or {}
        if not binding.get("connection_version"):
            return "This request predates your saved connection. Submit a new request with your saved key."
        saved = model_connections.metadata(access)
        if not saved:
            return "Connect your OpenRouter key, then submit a new request. The original connection is unavailable."
        if saved["version"] != binding["connection_version"]:
            return "Your OpenRouter key changed. Submit a new request with your current saved connection."
    return None


def failure_hint(saved):
    codes = [c.get("http_status") for c in saved.get("model_calls", []) if c.get("http_status")]
    code = codes[-1] if codes else None
    if code in {401, 403}:
        return "OpenRouter rejected the key or its permissions. Check your connection; replacing the key requires a new request."
    if code == 402:
        return "OpenRouter reported a credit or billing limit. Check your account balance and key spending limit before retrying."
    if code == 429:
        return "OpenRouter reported a rate limit. Wait for the limit to reset before retrying."
    if isinstance(code, int) and code >= 500:
        return "The model provider is temporarily unavailable. Retry when the service has recovered."
    return "Resolve the service issue before retrying. Completed claims remain saved; retry does not recheck them."


def request_details(job):
    payload, saved = json.loads(job["payload"]), json.loads(job["result"])
    models = (payload.get("openrouter") or {}).get("models") or saved.get("models") or {}
    calls = list(saved.get("parsing_calls", [])) + list(saved.get("model_calls", []))
    for result in saved.get("results", []):
        calls.extend(result.get("model_calls", []))
    with st.expander("Models and request details"):
        st.text("Reasoning model for this request: " + (models.get("text_model") or "Not recorded"))
        if models.get("vision_model"):
            st.text("Vision model for this request: " + models["vision_model"])
        st.caption("These are the models saved when this request was submitted. Editing your connection affects new requests.")
        if calls:
            fields = ("provider", "requested_model", "actual_model", "model", "status", "http_status", "provider_request_id")
            rows = [{field: call.get(field) for field in fields
                     if isinstance(call.get(field), (str, int, float, bool))} for call in calls]
            st.dataframe(rows, hide_index=True)
            st.caption("Observed request IDs can help you match calls in OpenRouter activity. Usage and billing remain in your OpenRouter account.")
        else:
            st.caption("No observed model calls are recorded for this request yet.")


@st.fragment(run_every=2)
def live_updates(access, key, revision):
    # Fragments do not rerun the login flow. Re-enter it when this access expires.
    _ensure_access(access)
    try:
        snapshot = store.progress_snapshot(access)
    except PermissionError:
        st.rerun()
    except Exception:
        st.warning("Live updates are temporarily unavailable. Use Refresh status to try again.")
        return
    fingerprint = (tuple(snapshot["documents"]), snapshot.get("connection"),
                   tuple((j["id"], j["status"], j["completed"]) for j in snapshot["jobs"]))
    previous = st.session_state.get(key+"live_fingerprint")
    previous_revision = st.session_state.get(key+"live_revision")
    st.session_state[key+"live_fingerprint"] = fingerprint
    st.session_state[key+"live_revision"] = revision
    # Only timer reruns may refresh the page. Interrupting an ordinary render here
    # could discard a form submission before its handler runs below this panel.
    if previous_revision == revision and previous is not None and previous != fingerprint:
        st.rerun()
    healthy = snapshot["worker_available"]
    if healthy:
        st.caption("Processing service available")
        st.caption("Live updates every 2 seconds. Results appear automatically; you can leave this page while work continues.")
    else:
        st.warning("Processing service unavailable — queued requests remain saved. Running work may need a retry when the service returns.")
        age = snapshot.get("worker_age_seconds")
        if age is None:
            st.caption("The processing service has not checked in yet.")
        elif age >= 60:
            st.caption(f"Last processing-service update: {int(age // 60)} minutes ago.")
        else:
            st.caption("The processing service has stopped checking in.")
        st.caption("This page checks service status every 2 seconds. Processing can resume when the service returns; contact the app administrator if it stays unavailable.")
    active = [j for j in snapshot["jobs"] if j["status"] in {"queued", "running"}]
    for job in active:
        label = {"verify": "Review", "process": "Document processing", "delete": "Document removal"}.get(job["kind"], "Request")
        state = "Queued" if job["status"] == "queued" else "In progress"
        if healthy:
            panel = st.status(f"{label} · {state}", state="running", expanded=True)
        else:
            panel = st.container(border=True)
            panel.text(f"{label} · Waiting for processing service")
        panel.text(job["name"] or label)
        panel.text(job["stage"])
        if job["kind"] == "verify" and job["total"]:
            done, total = job["completed"], job["total"]
            panel.progress(min(done / total, 1.0), text=f"{done} of {total} claims checked")


@st.fragment
def connection_panel(access, key):
    _ensure_access(access)
    try:
        saved = model_connections.metadata(access)
    except PermissionError:
        st.rerun()
    models = saved["models"] if saved else {}
    with st.expander("My OpenRouter connection", expanded=not bool(saved)):
        st.caption("Your OpenRouter account pays for reasoning and vision calls. Document/query embeddings use the app's Google service.")
        if saved:
            st.success("Your OpenRouter key is saved encrypted.")
            st.text("Saved reasoning model: " + models.get("text_model", "Not set"))
            st.text("Saved vision model: " + (models.get("vision_model") or "Not set"))
        text_model = draft_text("Reasoning model ID", key+"text_model", models.get("text_model", ""), max_chars=200,
                                help="Use the exact OpenRouter ID, including :free when appropriate. Strict JSON Schema support is required.")
        vision_model = draft_text("Vision model ID", key+"vision_model", models.get("vision_model", ""), max_chars=200,
                                  help="Document processing needs an image-capable model with strict JSON Schema support. It can be the same model as reasoning.")
        st.caption("Model edits apply after Check and save connection. Model choices stay here if the check fails.")
        with st.form(key+"connection", clear_on_submit=True):
            api_key = st.text_input("OpenRouter API key", type="password", max_chars=512,
                                    help="Paste your own key. Leave blank to keep your saved key.")
            submitted = st.form_submit_button("Check and save connection")
        if submitted:
            try:
                with st.spinner("Checking your OpenRouter key and model capabilities…"):
                    model_connections.save(access, api_key, text_model, vision_model)
            except PermissionError:
                st.rerun()
            except model_connections.ModelConnectionError as exc:
                st.error(str(exc))
            else:
                st.success("Connection checked and saved. New work will use these models and your OpenRouter credits.")
                st.rerun()
        if saved and st.button("Remove my saved key", key=key+"remove_key"):
            model_connections.remove(access)
            st.success("Saved key removed. Running work may finish; new attempts cannot use this connection.")
            st.rerun()
        st.caption("Queued jobs retain their selected models. Replacing/removing a key requires a new request. A valid key does not guarantee available account credits.")
    return bool(saved)


def action_hint(result):
    if result.get("status") == "error":
        return "Verification failed — retry when the service is available."
    text = str(result.get("reason", "")).lower()
    if any(x in text for x in ("period", "year", "quarter")):
        return "Specify the reporting year and annual or quarterly period in the claim, then submit a new review."
    if "conflict" in text:
        return "Choose the authoritative source and submit a new review."
    if "chart" in text:
        return "Inspect the cited page and check whether its values are readable."
    return "Inspect the evidence or add another source before submitting a new review."


def document_context(access, doc):
    meta, confirmed = json.loads(doc["metadata"]), json.loads(doc["context"])
    st.caption("Source context · " + _plain_label(doc["name"]))
    st.write({"Detected company": meta.get("company_name") or "Not established", "Reporting period": meta.get("reporting_period") or "Not established", "Currency": meta.get("currency") or "Not established"})
    with st.expander("Confirm or correct source context"):
        st.caption("These labels are review notes. They do not replace source evidence or silently change claim periods or currencies.")
        with st.form("context_" + doc["id"]):
            company = st.text_input("Company", value=confirmed.get("company", meta.get("company_name") or ""))
            period = st.text_input("Reporting period", value=confirmed.get("period", ""))
            currency = st.text_input("Currency", value=confirmed.get("currency", ""))
            if st.form_submit_button("Save context"):
                store.save_context(access, doc["id"], company, period, currency)
                st.success("Source context saved.")


def documents_page(access, key, connected=True):
    st.header("Documents")
    limit = int(os.getenv("CUSTOMER_MAX_UPLOAD_MB", "10"))
    st.caption(f"PDF only · Up to {limit} MB and {os.getenv('CUSTOMER_MAX_PAGES','500')} pages per document. Upload starts processing automatically.")
    with st.expander("Advanced processing options"):
        ocr = st.checkbox("Read scanned pages with OCR", value=True, key=key+"ocr")
        charts = st.slider("Maximum chart candidate pages", 1, 30, 20, key=key+"charts")
        st.caption("Candidate scans can miss charts. Source files are sent to the configured processing providers where required.")
    epoch = st.session_state.get(key+"upload_epoch", 0)
    if not connected:
        st.info("Connect your OpenRouter key in Settings before uploading a new source.")
    files = st.file_uploader("Upload source reports", type=["pdf"], accept_multiple_files=True,
                             key=key+f"uploads_{epoch}", disabled=not connected)
    for uploaded in files:
        doc = store.upload(access, uploaded.name, uploaded.getvalue(), {"enable_ocr": ocr, "visual_limit": charts})
        st.session_state[key+"active_document"] = doc
    if files:
        st.session_state[key+"upload_epoch"] = epoch+1
        st.rerun()
    example = os.getenv("CUSTOMER_EXAMPLE_PDF")
    if example and st.button("Try the prepared public report", key=key+"example", disabled=not connected):
        source = Path(example)
        doc = store.upload(access, source.name, source.read_bytes(), {"enable_ocr": True, "visual_limit": 20})
        st.session_state[key+"active_document"] = doc
        st.success("Example queued in your private workspace. Open Reviews after processing finishes.")
    available_documents = store.documents(access)
    for doc in available_documents:
        with st.expander(f"{_plain_label(doc['name'])} · {doc['status']}"):
            document_context(access, doc)
            meta = json.loads(doc["metadata"])
            st.write({"Pages": meta.get("page_count"), "Tables": meta.get("table_count"), "Charts": meta.get("chart_count")})
            for warning in meta.get("warnings", []):
                st.warning(warning)
            private_media.download(st, access, "Download original PDF",
                                   lambda doc_id=doc["id"]: store.document_bytes(access, doc_id),
                                   filename=doc["name"], mime="application/pdf", key=key+"download_"+doc["id"],
                                   revision=json.dumps([doc["sha"], doc["name"]]))
            with st.form(key+"delete_"+doc["id"]):
                confirm = st.checkbox("Delete this source and its index. Saved review snapshots will remain.")
                if st.form_submit_button("Delete document"):
                    if confirm:
                        store.request_delete(access, doc["id"])
                        st.success("Deletion queued.")
                    else:
                        st.warning("Confirm deletion first.")
    processing = [j for j in store.jobs(access) if j["kind"] != "verify"]
    for job in processing[:10]:
        st.caption(f"{job['kind'].title()}: {job['status']} · {job['stage']}")
        if job["status"] in {"failed", "paused"}:
            reason = retry_unavailable_reason(access, job)
            if reason:
                st.caption(reason)
            elif st.button("Retry processing", key=key+"retry_"+job["id"]):
                store.resume(access, job["id"]); st.rerun()
        source_id = json.loads(job["payload"]).get("document_id")
        source = next((doc for doc in available_documents if doc["id"] == source_id), None)
        if (connected and source and source["status"] not in {"ready", "deleting"} and
            job["kind"] == "process" and job["status"] in {"paused", "failed"}):
            if st.button("Start new processing with my saved connection", key=key+"new_process_"+job["id"]):
                store.restart_processing(access, job["id"]); st.rerun()


def _valid_citations(result, documents):
    allowed = {d["id"] for d in documents}
    return [e for e in result.get("evidence", []) if e.get("document_id") in allowed
            and isinstance(e.get("page"), int) and not isinstance(e["page"], bool) and e["page"] >= 1]


def calculation_sources(access, result, key, viewer_key):
    calculation = result.get("calculation") or {}
    addresses = calculation.get("source_inputs") or {}
    if not addresses:
        return
    documents = store.documents(access)
    names = {d["id"]: d["name"] for d in documents}
    citations = _valid_citations(result, documents)
    rows = []
    with st.expander("Calculation source pages", expanded=True):
        for name, address in addresses.items():
            meta = (calculation.get("input_metadata") or {}).get(name) or {}
            doc, page = address.get("document_id"), address.get("page")
            rows.append({"Input": name, "Source value": (calculation.get("raw_inputs") or {}).get(name),
                         "Normalized value": (calculation.get("inputs") or {}).get(name),
                         "Unit": meta.get("currency") or meta.get("unit") or calculation.get("currency") or "Not recorded",
                         "Document": names.get(doc, "Unavailable source"), "PDF page": page})
            matches = [i for i, e in enumerate(citations) if e["document_id"] == doc and e["page"] == page
                       and (e.get("id") == address.get("evidence_id") or e.get("source_evidence_id") == address.get("evidence_id"))]
            if not matches:
                st.warning(f"The source citation for input {name} is unavailable in this saved review.")
            elif st.button(f"View input page: {name}", key=key+"_"+name):
                st.session_state[viewer_key+"citation"] = matches[0]
                st.session_state[viewer_key+"page"] = page
        st.dataframe(rows, hide_index=True)


def viewer(access, result, key, versions=None):
    evidence = result.get("evidence") or []
    if not evidence:
        st.info("No cited source page is available for this result.")
        return
    documents = {d["id"]: d for d in store.documents(access)}
    available = _valid_citations(result, list(documents.values()))
    if not available:
        st.warning("The cited source is unavailable or has been deleted. The saved review retains its evidence snapshot.")
        return
    selected = st.selectbox("Selected citation", range(len(available)), format_func=lambda i: f"{documents[available[i]['document_id']]['name']} · PDF page {available[i]['page']} · {available[i].get('source_evidence_id') or available[i].get('id')}", key=key+"citation")
    item = available[selected]
    source = documents[item["document_id"]]
    if versions is not None and versions.get(item["document_id"]) != source.get("sha"):
        st.warning("The PDF version no longer matches this saved review. Its evidence snapshot remains available, but this citation cannot be verified against the current file.")
        return
    data = store.document_bytes(access, item["document_id"])
    signature = json.dumps([item["document_id"], source.get("sha"), item.get("source_evidence_id") or item.get("id"), item["page"]])
    if st.session_state.get(key+"citation_signature") != signature:
        st.session_state[key+"page"] = item["page"]
        st.session_state[key+"citation_signature"] = signature
        st.session_state.pop(key+"search_results", None)
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        if item["page"] > len(pdf):
            st.warning("The cited page is outside this PDF. Inspect the source before relying on this result.")
            return
        st.text(f"Source: {source['name']} · Selected citation: PDF page {item['page']}")
        with st.form(key+"search"):
            query = st.text_input("Search text in this PDF", max_chars=150)
            searched = st.form_submit_button("Search")
        if searched:
            matches = []
            if query.strip():
                for i in range(len(pdf)):
                    if pdf[i].search_for(query.strip()):
                        matches.append(i+1)
                        if len(matches) >= 30:
                            break
            st.session_state[key+"search_results"] = matches
        matches = st.session_state.get(key+"search_results")
        if matches is not None:
            st.caption(f"Matching pages (first 30): {', '.join(map(str,matches)) or 'None; scanned text may not be searchable.'}")
        page_number = st.number_input("PDF page", min_value=1, max_value=len(pdf), step=1, key=key+"page")
        zoom = st.slider("Zoom", 75, 200, 100, 5, key=key+"zoom", format="%d%%")
        page = pdf[int(page_number)-1]
        scale = min(zoom/100, 2200/max(page.rect.width, page.rect.height))
        pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        box = item.get("bbox")
        if page_number == item["page"] and isinstance(box, (list,tuple)) and len(box) == 4:
            try:
                coords = list(map(float, box))
                space = item.get("bbox_space", "")
                if not all(math.isfinite(n) for n in coords):
                    raise ValueError()
                if space in {"pdf_points", "pdf"}:
                    rect = pymupdf.Rect(coords) * page.rotation_matrix
                    coords = [rect.x0/page.rect.width*image.width, rect.y0/page.rect.height*image.height, rect.x1/page.rect.width*image.width, rect.y1/page.rect.height*image.height]
                elif space in {"normalized", "normalized_0_1"}:
                    coords = [coords[i]*(image.width if i%2==0 else image.height) for i in range(4)]
                elif space in {"pixels", "image_pixels"}:
                    coords = [coords[i]*(image.width/float(item["width_px"]) if i%2==0 else image.height/float(item["height_px"])) for i in range(4)]
                else:
                    raise ValueError()
                if coords[2] <= coords[0] or coords[3] <= coords[1]:
                    raise ValueError()
                ImageDraw.Draw(image).rectangle(coords, outline="#006ce0", width=4)
                st.caption("Blue outline: selected evidence region.")
            except (ValueError, KeyError, TypeError, ZeroDivisionError):
                st.caption("Region coordinates are unavailable or unsupported; inspect the full page.")
        rendered = io.BytesIO()
        image.save(rendered, format="PNG")
        page_png = rendered.getvalue()
        private_media.image(st, access, page_png, width=image.width, caption=f"PDF page {page_number} of {len(pdf)}", zoom=zoom)
        st.caption("100% fits the page. Zoom in, then swipe sideways or focus the preview and use arrow keys to pan.")
        private_media.download(st, access, "Download this page image", lambda: page_png,
                               filename=f"page_{page_number}.png", mime="image/png", key=key+"page_download",
                               revision=hashlib.sha256(page_png).hexdigest())


def review_detail(access, job, key):
    payload, saved = json.loads(job["payload"]), json.loads(job["result"])
    st.subheader(_plain_label(payload.get("name", "Financial review")))
    if job["status"] in {"queued", "running"}:
        st.caption("Review in progress. Follow the live progress panel above; completed claims appear automatically.")
    else:
        st.caption(f"Status: {job['status']} · {job['stage']}")
    if job["status"] in {"failed", "paused"}:
        st.error("Verification failed or paused. This is not an UNKNOWN factual verdict.")
        st.info(failure_hint(saved))
        reason = retry_unavailable_reason(access, job)
        if reason:
            st.caption(reason)
        elif st.button("Retry / resume", key=key+"retry"):
            store.resume(access, job["id"]); st.rerun()
        if payload.get("claim"):
            with st.expander("Submitted claims"):
                st.text(payload["claim"])
    request_details(job)
    results = saved.get("results", [])
    if not results:
        if job["status"] in {"failed", "paused"}:
            st.info("No completed claims were saved. Resolve the service issue before retrying or submitting a new review.")
        elif job["status"] == "complete":
            st.info("This saved review contains no completed claims.")
        else:
            st.info("Results appear automatically as each claim is checked. You may close this tab and return later.")
        return
    decisions = store.decisions(access, job["id"])
    mode = st.radio("Show claims", ["Needs review", "Contradictions", "All"], horizontal=True, key=key+"filter")
    order = sorted(range(len(results)), key=lambda i: (results[i].get("verdict") not in {"CONTRADICTED", "UNKNOWN", "PARTIALLY_SUPPORTED"}, i))
    if mode == "Contradictions":
        order = [i for i in order if results[i].get("verdict") == "CONTRADICTED"]
    elif mode == "Needs review":
        order = [i for i in order if decisions.get(i, {}).get("decision") != "reviewed"]
    if not order:
        st.success("No claims match this filter.")
    else:
        index = st.selectbox("Claim", order, format_func=lambda i: f"{i+1}. {results[i].get('verdict','UNKNOWN')} · {results[i].get('claim_text','')[:100]}", key=key+"atom_"+mode)
        result = results[index]
        with st.container(key="customer_review_layout"):
            left, right = st.columns([1, 1.2])
            with left:
                label = "VERIFICATION FAILED" if execution_failed(result) else result.get("verdict", "UNKNOWN").replace("_", " ")
                st.markdown("### " + label)
                st.text(result.get("claim_text") or "")
                st.text(result.get("reason") or "")
                if result.get("verdict") == "UNKNOWN" or execution_failed(result):
                    st.info(action_hint(result))
                from ui.calculation import render_calculation
                render_calculation(result)
                calculation_sources(access, result, key+f"input_{index}", key+f"viewer_{index}")
                st.markdown("#### Evidence")
                for n, evidence in enumerate(result.get("evidence", [])):
                    st.caption(f"PDF page {evidence.get('page')} · {evidence.get('type','source')}")
                    rows = evidence.get("rows")
                    if rows:
                        metric = str((result.get("parsed_claim") or {}).get("metric") or "").lower()
                        relevant = [r for r in rows if metric and metric in " ".join(map(str,r)).lower()]
                        st.write(evidence.get("headers") or evidence.get("column_headers") or {})
                        st.dataframe(relevant or rows[:5], hide_index=True)
                        with st.expander("Full table", expanded=False):
                            st.dataframe(rows, hide_index=True)
                    else:
                        st.text(evidence.get("content", ""))
                    if st.button("View Page", key=key+f"view_{index}_{n}"):
                        # Viewer options are ownership-filtered; selection index is mapped below.
                        valid = _valid_citations(result, store.documents(access))
                        if evidence in valid:
                            st.session_state[key+f"viewer_{index}citation"] = valid.index(evidence)
                decision_editor(access, job["id"], index, key+f"decision_{index}_")
            with right:
                viewer(access, result, key+f"viewer_{index}", versions=payload.get("versions"))
    export_revision = hashlib.sha256(json.dumps([job["status"], job["payload"], job["result"], decisions], sort_keys=True).encode()).hexdigest()
    with st.container(key="customer_export_actions"):
        a, b = st.columns(2)
        with a:
            private_media.download(st, access, "Export CSV", lambda: exports(access, job["id"])[0],
                                   filename="financial_review.csv", mime="text/csv", key=key+"csv", revision=export_revision)
        with b:
            private_media.download(st, access, "Export readable report", lambda: exports(access, job["id"])[1],
                                   filename="financial_review.html", mime="text/html", key=key+"report", revision=export_revision)
        with st.expander("Advanced export"):
            private_media.download(st, access, "Download review JSON", lambda: exports(access, job["id"])[2],
                                   filename="financial_review.json", mime="application/json", key=key+"json", revision=export_revision)
            st.caption("Includes model/version metadata when available. Confidence values in raw results are uncalibrated.")


def reviews_page(access, key, connected=True):
    ready = [d for d in store.documents(access) if d["status"] == "ready"]
    st.header("Reviews")
    if not ready:
        st.info("Upload a report in Documents. Processing continues while you are away.")
    else:
        ids = [d["id"] for d in ready]
        names = {d["id"]: d["name"] for d in ready}
        if st.session_state.get(key+"active_document") not in ids:
            st.session_state[key+"active_document"] = ids[0]
        active = st.selectbox("Active source", ids, format_func=lambda d: names[d], key=key+"active_document")
        st.session_state[key+"additional"] = [d for d in st.session_state.get(key+"additional", []) if d in ids and d != active]
        more = st.multiselect("Additional sources (optional)", [d for d in ids if d != active], format_func=lambda d: names[d], max_selections=2, key=key+"additional")
        st.caption("Claims without a company name require exactly one source. With multiple sources, name the company explicitly. Specify the reporting period in each claim.")
        document_context(access, next(d for d in ready if d["id"] == active))
        review_composer(access, key, [active]+more, connected)
    reviews = store.jobs(access, "verify")
    if reviews:
        ids = [j["id"] for j in reviews]
        mapping = {j["id"]: j for j in reviews}
        if st.session_state.get(key+"selected_review") not in ids:
            st.session_state[key+"selected_review"] = ids[0]
        selected = st.selectbox("Saved reviews", ids, format_func=lambda j: f"{json.loads(mapping[j]['payload']).get('name')} · {mapping[j]['status']}", key=key+"selected_review")
        review_detail(access, store.get_job(access, selected), key+selected)


def open_saved_review(access, key, job_id):
    _ensure_access(access)
    try:
        job = store.get_job(access, job_id)
        if job["kind"] != "verify":
            raise PermissionError("Review unavailable.")
    except (PermissionError, ValueError):
        st.session_state.pop("customer_prepared_download", None)
        st.rerun()
        return
    st.session_state[key+"selected_review"] = job_id
    st.session_state[key+job_id+"filter"] = "All"
    st.session_state[key+"area"] = "Reviews"


def history_page(access, key):
    st.header("History")
    reviews = store.jobs(access, "verify")
    if not reviews:
        st.info("No saved reviews yet. Create a review from Reviews when a source is ready.")
        return
    st.dataframe([{"Review": json.loads(j["payload"]).get("name"), "Status": j["status"],
                   "Created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(j["created"])), "ID": j["id"]}
                  for j in reviews], hide_index=True)
    mapping = {j["id"]: j for j in reviews}
    if st.session_state.get(key+"history_review") not in mapping:
        st.session_state[key+"history_review"] = reviews[0]["id"]
    selected = st.selectbox("Open a saved review", list(mapping),
                            format_func=lambda j: f"{json.loads(mapping[j]['payload']).get('name')} · {mapping[j]['status']}",
                            key=key+"history_review")
    st.button("Open review", key=key+"history_open", on_click=open_saved_review, args=(access, key, selected))
    st.caption("Saved results, decisions and notes remain available after signing in again. Exports use the latest saved review.")


def run(access):
    _ensure_access(access)
    key = "customer_"+access.workspace+"_"
    st.title("Financial claim review")
    st.caption("Check the figures in your research note against original reports, with visible calculations and page citations.")
    try:
        connected = bool(model_connections.metadata(access))
    except PermissionError:
        st.rerun()
        return
    revision = st.session_state.get(key+"render_revision", 0)+1
    st.session_state[key+"render_revision"] = revision
    live_updates(access, key, revision)
    if st.button("Refresh status", key=key+"refresh", help="Optional fallback; progress and results update automatically."):
        st.rerun()
    area = navigation(key)
    try:
        if area == "Documents":
            documents_page(access, key, connected=connected)
        elif area == "Reviews":
            reviews_page(access, key, connected=connected)
        else:
            history_page(access, key)
    except (PermissionError, ValueError) as exc:
        st.error(str(exc))
    except Exception:
        st.error("This action could not be completed. Refresh or contact the administrator.")
