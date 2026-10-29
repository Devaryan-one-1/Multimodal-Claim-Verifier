from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import pymupdf
import streamlit as st
from PIL import Image, ImageDraw
from pilot import store
from pilot.exports import exports


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
    st.caption("Source context · " + doc["name"])
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


def documents_page(access, key):
    st.header("Documents")
    limit = int(os.getenv("CUSTOMER_MAX_UPLOAD_MB", "10"))
    st.caption(f"PDF only · Up to {limit} MB and {os.getenv('CUSTOMER_MAX_PAGES','500')} pages per document. Upload starts processing automatically.")
    with st.expander("Advanced processing options"):
        ocr = st.checkbox("Read scanned pages with OCR", value=True, key=key+"ocr")
        charts = st.slider("Maximum chart candidate pages", 1, 30, 20, key=key+"charts")
        st.caption("Candidate scans can miss charts. Source files are sent to the configured processing providers where required.")
    epoch = st.session_state.get(key+"upload_epoch", 0)
    files = st.file_uploader("Upload source reports", type=["pdf"], accept_multiple_files=True, key=key+f"uploads_{epoch}")
    for uploaded in files:
        doc = store.upload(access, uploaded.name, uploaded.getvalue(), {"enable_ocr": ocr, "visual_limit": charts})
        st.session_state[key+"active_document"] = doc
    if files:
        st.session_state[key+"upload_epoch"] = epoch+1
        st.rerun()
    example = os.getenv("CUSTOMER_EXAMPLE_PDF")
    if example and st.button("Try the prepared public report", key=key+"example"):
        source = Path(example)
        doc = store.upload(access, source.name, source.read_bytes(), {"enable_ocr": True, "visual_limit": 20})
        st.session_state[key+"active_document"] = doc
        st.success("Example queued in your private workspace. Open Reviews after processing finishes.")
    if st.button("Refresh documents", key=key+"refresh_docs"):
        st.rerun()
    for doc in store.documents(access):
        with st.expander(f"{doc['name']} · {doc['status']}"):
            document_context(access, doc)
            meta = json.loads(doc["metadata"])
            st.write({"Pages": meta.get("page_count"), "Tables": meta.get("table_count"), "Charts": meta.get("chart_count")})
            for warning in meta.get("warnings", []):
                st.warning(warning)
            st.download_button("Download original PDF", store.document_bytes(access, doc["id"]), file_name=doc["name"], mime="application/pdf", key=key+"download_"+doc["id"])
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
        if job["status"] in {"paused", "failed"} and job["attempts"] < 3:
            if st.button("Retry processing", key=key+"retry_"+job["id"]):
                store.resume(access, job["id"]); st.rerun()


def viewer(access, result, key):
    evidence = result.get("evidence") or []
    if not evidence:
        st.info("No cited source page is available for this result.")
        return
    allowed = {d["id"] for d in store.documents(access)}
    available = [e for e in evidence if e.get("document_id") in allowed and isinstance(e.get("page"), int)]
    if not available:
        st.warning("The cited source is unavailable or has been deleted. The saved review retains its evidence snapshot.")
        return
    selected = st.selectbox("Selected citation", range(len(available)), format_func=lambda i: f"Page {available[i]['page']} · {available[i].get('source_evidence_id') or available[i].get('id')}", key=key+"citation")
    item = available[selected]
    data = store.document_bytes(access, item["document_id"])
    signature = str(item.get("id"))+str(item["page"])
    if st.session_state.get(key+"citation_signature") != signature:
        st.session_state[key+"page"] = item["page"]
        st.session_state[key+"citation_signature"] = signature
        st.session_state.pop(key+"search_results", None)
    with pymupdf.open(stream=data, filetype="pdf") as pdf:
        st.caption(f"Selected citation: PDF page {item['page']}")
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
        zoom = st.slider("Zoom", 75, 200, 110, 5, key=key+"zoom")
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
        st.image(image, width=image.width, caption=f"PDF page {page_number} of {len(pdf)}")
        st.download_button("Download this page image", pix.tobytes("png"), file_name=f"page_{page_number}.png", mime="image/png", key=key+"page_download")


def review_detail(access, job, key):
    payload, saved = json.loads(job["payload"]), json.loads(job["result"])
    st.subheader(payload.get("name", "Financial review"))
    st.caption(f"Status: {job['status']} · {job['stage']}")
    if job["status"] in {"failed", "paused"}:
        st.error("Verification failed or paused. This is not an UNKNOWN factual verdict.")
        if job["attempts"] < 3 and st.button("Retry / resume", key=key+"retry"):
            store.resume(access, job["id"]); st.rerun()
    results = saved.get("results", [])
    if not results:
        st.info("Results will appear here when the worker completes a claim. You may close this tab and return later.")
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
        left, right = st.columns([1, 1.2])
        with left:
            label = "VERIFICATION FAILED" if result.get("status") == "error" else result.get("verdict", "UNKNOWN").replace("_", " ")
            st.markdown("### " + label)
            st.write(result.get("claim_text"))
            st.write(result.get("reason"))
            if result.get("verdict") == "UNKNOWN" or result.get("status") == "error":
                st.info(action_hint(result))
            from ui.calculation import render_calculation
            render_calculation(result)
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
                    st.write(evidence.get("content", ""))
                if st.button("View Page", key=key+f"view_{index}_{n}"):
                    # Viewer options are ownership-filtered; selection index is mapped below.
                    allowed = {d["id"] for d in store.documents(access)}
                    valid = [e for e in result.get("evidence", []) if e.get("document_id") in allowed and isinstance(e.get("page"), int)]
                    if evidence in valid:
                        st.session_state[key+f"viewer_{index}citation"] = valid.index(evidence)
            prior = decisions.get(index, {})
            choices = ["unreviewed", "reviewed", "incorrect_evidence"]
            with st.form(key+f"decision_{index}"):
                choice = st.selectbox("Review decision", choices, index=choices.index(prior.get("decision", "unreviewed")))
                note = st.text_area("Review note", value=prior.get("note", ""), max_chars=2000)
                if st.form_submit_button("Save decision"):
                    store.decide(access, job["id"], index, choice, note, expected_updated=prior.get("updated"))
                    st.rerun()
        with right:
            viewer(access, result, key+f"viewer_{index}")
    csv_data, report, raw = exports(access, job["id"])
    a, b = st.columns(2)
    a.download_button("Export CSV", csv_data, file_name="financial_review.csv", mime="text/csv", key=key+"csv")
    b.download_button("Export readable report", report, file_name="financial_review.html", mime="text/html", key=key+"report")
    with st.expander("Advanced export"):
        st.download_button("Download review JSON", raw, file_name="financial_review.json", mime="application/json", key=key+"json")
        st.caption("Includes model/version metadata when available. Confidence values in raw results are uncalibrated.")


def reviews_page(access, key):
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
        with st.form(key+"new_review"):
            name = st.text_input("Review name", value="Financial review", max_chars=160)
            claim = st.text_area("Claims to check", height=140, max_chars=6000, placeholder="Include the company, metric and reporting period. Compound claims are checked separately.")
            if st.form_submit_button("Check figures", type="primary"):
                job = store.submit_review(access, name, claim, [active]+more)
                st.session_state[key+"selected_review"] = job
                st.success("Review queued. Completed claims and review decisions will be saved.")
    reviews = store.jobs(access, "verify")
    if reviews:
        ids = [j["id"] for j in reviews]
        mapping = {j["id"]: j for j in reviews}
        if st.session_state.get(key+"selected_review") not in ids:
            st.session_state[key+"selected_review"] = ids[0]
        selected = st.selectbox("Saved reviews", ids, format_func=lambda j: f"{json.loads(mapping[j]['payload']).get('name')} · {mapping[j]['status']}", key=key+"selected_review")
        review_detail(access, store.get_job(access, selected), key+selected)


def run(access):
    key = "customer_"+access.workspace+"_"
    st.title("Financial claim review")
    st.caption("Check the figures in your research note against original reports, with visible calculations and page citations.")
    with store.db() as c:
        store.authorize(c, access)
        row = c.execute("SELECT heartbeat FROM worker_status WHERE id=1").fetchone()
    healthy = bool(row and time.time()-row["heartbeat"] < 15)
    st.caption("Processing service available" if healthy else "Processing service unavailable — queued requests remain saved.")
    if st.button("Refresh status", key=key+"refresh"):
        st.rerun()
    area = st.radio("Navigation", ["Reviews", "Documents", "History"], horizontal=True, key=key+"area")
    try:
        if area == "Documents":
            documents_page(access, key)
        elif area == "Reviews":
            reviews_page(access, key)
        else:
            st.header("History")
            reviews = store.jobs(access, "verify")
            st.dataframe([{"Review": json.loads(j["payload"]).get("name"), "Status": j["status"], "Created": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(j["created"])), "ID": j["id"]} for j in reviews], hide_index=True)
            st.caption("Open a saved review from Reviews to inspect sources, save decisions and export it.")
    except (PermissionError, ValueError) as exc:
        st.error(str(exc))
    except Exception:
        st.error("This action could not be completed. Refresh or contact the administrator.")
