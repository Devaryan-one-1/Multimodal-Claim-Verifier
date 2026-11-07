"""Local configuration/storage checks. No provider calls or secret values printed."""
from contextlib import closing
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from urllib.parse import urlparse


def configuration_errors(env):
    errors = []
    if env.get("CUSTOMER_STORAGE", "local").lower() not in {"local", "supabase"}:
        errors.append("Set CUSTOMER_STORAGE to local or supabase.")
    url = urlparse(env.get("SUPABASE_URL", ""))
    if (url.scheme != "https" or not url.hostname or url.username or url.password or
            url.path not in {"", "/"} or url.query or url.fragment or "YOUR_" in url.netloc.upper()):
        errors.append("Set a valid HTTPS Supabase project URL.")
    publishable = env.get("SUPABASE_PUBLISHABLE_KEY", "")
    if not publishable.startswith("sb_publishable_") or "REPLACE" in publishable.upper():
        errors.append("Set the Supabase publishable key; service-role keys are not accepted.")
    site = urlparse(env.get("SUPABASE_SITE_URL", ""))
    local = site.hostname in {"localhost", "127.0.0.1", "::1"}
    if not site.hostname or site.username or site.password or site.query or site.fragment or site.scheme not in {"https", "http"} or (site.scheme == "http" and not local):
        errors.append("Use an HTTPS customer Site URL, or loopback HTTP for local testing.")
    if env.get("BETA_PRODUCTION", "0") == "1" and (site.scheme != "https" or local):
        errors.append("Production requires the actual public HTTPS Site URL.")
    try:
        if len(base64.urlsafe_b64decode(env.get("CUSTOMER_KEY_ENCRYPTION_KEY", ""))) != 32:
            raise ValueError()
    except Exception:
        errors.append("Set the preserved encryption master key; do not regenerate it on restart.")
    google = env.get("GOOGLE_API_KEY") or env.get("GEMINI_API_KEY") or ""
    if not google or "REPLACE" in google.upper():
        errors.append("Set the operator Google embedding key.")
    if env.get("OPENROUTER_API_KEY"):
        errors.append("Remove the operator OpenRouter key from this BYOK deployment.")
    if not re.fullmatch(r"[a-f0-9]{40}", env.get("RERANK_MODEL_REVISION", "")):
        errors.append("Pin the reranker to a full model commit revision.")
    for name, default in {"CUSTOMER_MAX_UPLOAD_MB": "10", "CUSTOMER_MAX_PAGES": "500",
                          "CUSTOMER_MAX_DOCUMENTS": "30", "CUSTOMER_JOBS_PER_DAY": "100",
                          "CUSTOMER_MAX_PAGE_PIXELS": "25000000", "CUSTOMER_MAX_RENDER_DIMENSION": "10000",
                          "CUSTOMER_JOB_TIMEOUT_SECONDS": "1800", "GEMINI_EMBEDDING_DIMENSIONS": "768"}.items():
        try:
            if int(env.get(name, default)) <= 0:
                raise ValueError()
        except ValueError:
            errors.append(name+" must be a positive integer.")
    return errors


def check(env=None):
    env = os.environ if env is None else env
    errors = configuration_errors(env)
    if errors:
        return errors
    if env.get("CUSTOMER_STORAGE", "local").lower() == "supabase":
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]/"app"))
            from pilot.cloud_db import initialize, db
            from pilot.cloud_files import storage
            from cryptography.fernet import Fernet
            cipher = Fernet(env["CUSTOMER_KEY_ENCRYPTION_KEY"].encode())
            initialize()
            storage()
            with db() as c:
                expected_model=env.get("GEMINI_EMBEDDING_MODEL","gemini-embedding-001")
                for row in c.execute("SELECT model,dimensions FROM vector_collections"):
                    if row["model"] != expected_model or row["dimensions"] != 768:
                        raise ValueError()
                for row in c.execute("SELECT principal,version,secret FROM model_connections"):
                    value = json.loads(cipher.decrypt(row["secret"].encode()))
                    if value.get("principal") != row["principal"] or value.get("version") != row["version"]:
                        raise ValueError()
            if int(env.get("GEMINI_EMBEDDING_DIMENSIONS", "768")) != 768:
                raise ValueError()
        except Exception:
            return ["Cloud storage preflight failed. Check the private schema/bucket, session-pooler URL, preserved master key and 768-dimensional settings."]
        return []
    try:
        from cryptography.fernet import Fernet
        cipher = Fernet(env["CUSTOMER_KEY_ENCRYPTION_KEY"].encode())
        root = Path(env.get("CUSTOMER_DATA_ROOT", "/var/lib/verifier/customer_data")).resolve()
        if not root.is_relative_to(Path("/var/lib/verifier")):
            return ["Customer data must be inside the mounted /var/lib/verifier volume."]
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=root):
            pass
        if shutil.disk_usage(root).free < 1024**3:
            errors.append("Less than 1 GiB is free on the customer data volume.")
        control = root/"control.sqlite3"
        if control.exists():
            with closing(sqlite3.connect(control.as_uri()+"?mode=ro", uri=True)) as c:
                if c.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    errors.append("The customer control database failed its integrity check.")
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='model_connections'").fetchone():
                    for principal, version, secret in c.execute("SELECT principal,version,secret FROM model_connections"):
                        value = json.loads(cipher.decrypt(secret.encode()))
                        if value.get("principal") != principal or value.get("version") != version:
                            raise ValueError()
        model, dimensions = env.get("GEMINI_EMBEDDING_MODEL", "gemini-embedding-001"), int(env.get("GEMINI_EMBEDDING_DIMENSIONS", "768"))
        collection = "evidence_v2_"+hashlib.sha256(f"{model}:{dimensions}".encode()).hexdigest()[:12]
        for index in root.glob("workspaces/*/data/chroma/chroma.sqlite3"):
            with closing(sqlite3.connect(index.resolve().as_uri()+"?mode=ro", uri=True)) as c:
                rows = c.execute("SELECT dimension FROM collections WHERE name=?", (collection,)).fetchall()
                if not rows and c.execute("SELECT 1 FROM collections LIMIT 1").fetchone():
                    errors.append("The configured embedding model/dimensions do not match a saved index. Restore the original settings; do not rebuild it automatically.")
                if any(row[0] is not None and row[0] != dimensions for row in rows):
                    errors.append("A saved index has incompatible embedding dimensions. Preserve it and restore its original settings.")
    except Exception:
        errors.append("Storage or encrypted connections could not be verified. Check volume permissions and the preserved master key.")
    return errors


if __name__ == "__main__":
    failures = check()
    print("\n".join(failures) if failures else "Preflight passed.")
    raise SystemExit(bool(failures))
