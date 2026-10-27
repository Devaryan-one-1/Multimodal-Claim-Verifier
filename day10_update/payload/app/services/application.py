"""Stable dashboard service boundary. Existing result and callback contracts stay intact."""
from __future__ import annotations


def process_document(*args, **kwargs):
    from pipelines.document_pipeline import process_document as run
    return run(*args, **kwargs)


def saved_documents():
    from pipelines.document_pipeline import saved_documents as load
    return load()


def verify_claims(claim_text, document_ids, reporter=None):
    from pipelines.verification_pipeline import verify_claims as run
    return run(claim_text, document_ids, reporter=reporter)


def aggregate_verdict(results):
    from pipelines.verification_pipeline import aggregate_verdict as aggregate
    return aggregate(results)


def render_page(*args, **kwargs):
    from extraction.image_renderer import render_page as render
    return render(*args, **kwargs)
