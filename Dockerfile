# syntax=docker/dockerfile:1
FROM python:3.12-slim-trixie AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/verifier-models HF_HUB_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1 \
    TOKENIZERS_PARALLELISM=false ANONYMIZED_TELEMETRY=false \
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 MKL_NUM_THREADS=2 \
    RERANK_MODEL_NAME=cross-encoder/ms-marco-MiniLM-L6-v2 \
    RERANK_MODEL_REVISION=233902d25c440f23af6f7d6e94d2946bac0bee0a
WORKDIR /app
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 verifier \
    && useradd --uid 10001 --gid 10001 --create-home verifier
COPY requirements-production.txt /app/requirements-production.txt
RUN python -m pip install --no-cache-dir --upgrade pip==26.3 \
    && python -m pip install --no-cache-dir --index-url https://download.pytorch.org/whl/cpu torch==2.14.0 \
    && python -m pip install --no-cache-dir -r requirements-production.txt \
    && python -m pip check
COPY app/pilot/ /app/app/pilot/
COPY app/customer_main.py app/pilot_worker.py app/pilot_task.py app/pilot_admin.py /app/app/
COPY app/extraction/ /app/app/extraction/
COPY app/pipelines/ /app/app/pipelines/
COPY app/retrieval/ /app/app/retrieval/
COPY app/verification/ /app/app/verification/
COPY app/reasoning/ /app/app/reasoning/
COPY app/services/ /app/app/services/
COPY app/ui/calculation.py /app/app/ui/calculation.py
COPY scripts/ /app/scripts/
COPY deploy/*.py /app/deploy/
RUN python -c "import streamlit, supabase, pymupdf, pdfplumber, torch, httpx, jsonschema; print('Dependency imports OK')" \
    && HF_HUB_OFFLINE=0 python deploy/warm_model.py \
    && python -m pip freeze --all > /app/deploy/installed-requirements.txt \
    && mkdir -p /var/lib/verifier /backups /backup-secrets \
    && chown -R 10001:10001 /var/lib/verifier /backups /backup-secrets \
    && chmod 700 /backups /backup-secrets \
    && chmod -R a+rX /opt/verifier-models
COPY supabase/storage_schema.sql /app/supabase/storage_schema.sql
USER 10001:10001
ENTRYPOINT ["python", "deploy/entrypoint.py"]
CMD ["web"]

# Separate branch: Chroma remains available for local fixtures and migration.
# It is never installed in an ancestor of the customer cloud release.
FROM runtime AS local-release
USER root
COPY requirements-local.txt /app/requirements-local.txt
RUN python -m pip install --no-cache-dir -r requirements-local.txt \
    && python -m pip check \
    && python -m pip freeze --all > /app/deploy/installed-requirements.txt
ENV CUSTOMER_STORAGE=local BETA_PRODUCTION=0 CUSTOMER_MAX_UPLOAD_MB=10
USER 10001:10001

FROM local-release AS local-security-audit
USER root
RUN python -m pip install --no-cache-dir pip-audit==2.9.0 \
    && python scripts/dependency_audit.py

FROM runtime AS cloud-runtime
ENV CUSTOMER_STORAGE=supabase BETA_PRODUCTION=1 CUSTOMER_MAX_UPLOAD_MB=4 \
    CUSTOMER_CACHE_ROOT=/tmp/claim-verifier-cache \
    CLAIM_VERIFIER_LOG_DIR=/tmp/claim-verifier-logs PORT=8501
USER root
RUN python -c "from importlib.util import find_spec; from importlib.metadata import packages_distributions; assert find_spec('chromadb') is None; assert 'chromadb' not in packages_distributions(); print('Cloud release contains no Chroma')" \
    && touch /app/deploy/cloud-only
USER 10001:10001

FROM cloud-runtime AS security-audit
USER root
RUN python -m pip install --no-cache-dir pip-audit==2.9.0 \
    && python scripts/dependency_audit.py

FROM cloud-runtime AS release
