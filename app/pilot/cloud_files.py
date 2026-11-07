"""Private immutable objects, verified manifests and disposable worker caches."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
from urllib.parse import urlparse

from pilot.cloud_db import CloudStorageError

DIRECTORIES = {"documents", "processed", "pages", "tables", "charts", "table_images", "model_cache"}
BUCKET = "verifier-private"


def relative_path(value, document_id=None):
    value = str(value)
    path = PurePosixPath(value)
    parts = path.parts
    if (not value or path.is_absolute() or str(path) != value
            or any(p in {".", ".."} for p in parts)
            or not re.fullmatch(r"[A-Za-z0-9_./-]{1,1024}", value)
            or len(parts) < 4 or parts[0] != "data"):
        raise CloudStorageError("Invalid private file path.")
    if parts[1] in DIRECTORIES:
        from pilot.store import uid
        uid(parts[2])
        if document_id is not None and parts[2] != document_id:
            raise CloudStorageError("Private file belongs to a different document.")
    elif parts[1:3] != ("cache", "query_embeddings") or document_id is not None:
        raise CloudStorageError("Unsupported private file path.")
    return value


@lru_cache(maxsize=1)
def storage():
    url, key = os.getenv("SUPABASE_URL", ""), os.getenv("SUPABASE_SECRET_KEY", "")
    parsed = urlparse(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
            or not key.startswith(("sb_secret_","eyJ")) or "REPLACE" in key.upper()):
        raise CloudStorageError("Configure the server-only SUPABASE_SECRET_KEY and HTTPS project URL for private files.")
    http = None
    try:
        import httpx
        from storage3 import SyncStorageClient
        headers={"apikey":key}
        if key.startswith("eyJ"):
            headers["Authorization"]="Bearer "+key
        # New sb_secret keys are API keys, not bearer JWTs. Avoid auth-session state.
        http=httpx.Client(headers=headers,timeout=30,follow_redirects=False)
        client=SyncStorageClient(url.rstrip("/")+"/storage/v1",headers,http_client=http)
        bucket = client.get_bucket(BUCKET)
        public = bucket.get("public") if isinstance(bucket, dict) else bucket.public
        if public is not False:
            raise ValueError()
        return client.from_(BUCKET)
    except Exception:
        if http is not None:
            http.close()
        raise CloudStorageError("Private file storage is unavailable. Apply the supplied private-bucket schema and check the server secret.") from None


def object_key(workspace, path, sha):
    from pilot.store import uid
    path = relative_path(path)
    if not re.fullmatch(r"[a-f0-9]{64}",sha):
        raise CloudStorageError("Invalid private file checksum.")
    parts = PurePosixPath(path).parts
    folder = parts[2] if parts[1] in DIRECTORIES else "_cache"
    return uid(workspace)+"/"+folder+"/"+sha+"-"+hashlib.sha256(path.encode()).hexdigest()


def put(c, workspace, document_id, path, data):
    path = relative_path(path, document_id)
    sha = hashlib.sha256(data).hexdigest()
    key = object_key(workspace, path, sha)
    old = c.execute("SELECT object_key,sha FROM artifacts WHERE workspace=? AND path=?", (workspace,path)).fetchone()
    if old and old["sha"] == sha:
        return None
    try:
        # Idempotent immutable key: repeating a failed migration cannot change content.
        storage().upload(key, data, {"content-type": "application/octet-stream", "upsert": "true"})
    except Exception:
        raise CloudStorageError("Private file upload failed. Check storage quota and connectivity, then retry.") from None
    c.execute("INSERT INTO artifacts(workspace,document_id,path,object_key,sha,size) VALUES(?,?,?,?,?,?) "
              "ON CONFLICT(workspace,path) DO UPDATE SET document_id=excluded.document_id,object_key=excluded.object_key,sha=excluded.sha,size=excluded.size",
              (workspace,document_id,path,key,sha,len(data)))
    return old["object_key"] if old else None


def _download(key,sha,size):
    try:
        data = storage().download(key)
    except Exception:
        raise CloudStorageError("Private file download failed. Check storage availability, then retry.") from None
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
        raise CloudStorageError("Private file checksum failed. Restore the verified source; it has not been used for a verdict.")
    return data


# Bounded server memory only. Every caller still checks membership before/after use.
# Prevent a live UI rerun from downloading the same 10 MB PDF every two seconds.
cached_download = lru_cache(maxsize=4)(_download)


def download(row, *, cached=False):
    path = relative_path(row["path"], row["document_id"])
    if row["object_key"] != object_key(row["workspace"],path,row["sha"]):
        raise CloudStorageError("Private file manifest failed validation.")
    fetch = cached_download if cached else _download
    return fetch(row["object_key"],row["sha"],row["size"])


def read_document(access, document_id):
    from pilot import store
    with store.db() as c:
        source = store.document(c,access,document_id)
        row = c.execute("SELECT * FROM artifacts WHERE workspace=? AND path=?", (access.workspace,f"data/documents/{source['id']}/original.pdf")).fetchone()
        if not row:
            raise CloudStorageError("The saved PDF is unavailable in private storage. Restore it or upload it as a new source.")
        data = download(row,cached=True)
        store.authorize(c,access)
        if hashlib.sha256(data).hexdigest() != source["sha"]:
            raise CloudStorageError("The saved PDF does not match its source version.")
        return data


def hydrate(access, document_ids):
    from pilot import store
    base = store.workspace_dir(access.workspace)
    with store.db() as c:
        store.authorize(c,access)
        rows = []
        for doc in dict.fromkeys(document_ids):
            store.document(c,access,doc)
            rows.extend(c.execute("SELECT * FROM artifacts WHERE workspace=? AND document_id=?", (access.workspace,doc)).fetchall())
        rows.extend(c.execute("SELECT * FROM artifacts WHERE workspace=? AND document_id IS NULL", (access.workspace,)).fetchall())
    for row in rows:
        relative_path(row["path"],row["document_id"])
        target = store.file_path(access.workspace,*PurePosixPath(row["path"]).parts)
        if target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest() == row["sha"]:
            continue
        data = download(row)
        target.parent.mkdir(parents=True,exist_ok=True)
        temporary = target.with_name(target.name+".download")
        temporary.write_bytes(data)
        temporary.replace(target)
    with store.db() as c:
        store.authorize(c,access)
    return base


def document_files(base, document_id):
    from pilot.store import uid
    uid(document_id)
    for directory in sorted(DIRECTORIES):
        root = base/"data"/directory/document_id
        if any(p.is_symlink() or p.is_junction() for p in (base,base/"data",root.parent,root)):
            raise CloudStorageError("Symbolic links are not allowed in private artifacts.")
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root.resolve()):
                    raise CloudStorageError("Private artifact is outside its document directory.")
                if path.is_file() and not path.name.endswith((".download",".tmp")):
                    yield path


def publish(access, document_id, job_id, token):
    from pilot import store
    base = store.workspace_dir(access.workspace)
    old_keys = []
    with store.db() as c:
        store.document(c,access,document_id)
        if not c.execute("SELECT 1 FROM jobs WHERE id=? AND workspace=? AND status='running' AND run_token=?", (job_id,access.workspace,token)).fetchone():
            raise CloudStorageError("Processing ownership changed. Private artifacts were not published.")
        for path in document_files(base,document_id):
            old = put(c,access.workspace,document_id,path.relative_to(base).as_posix(),path.read_bytes())
            if old: old_keys.append(old)
        store.authorize(c,access)
    remove_objects(old_keys)


def publish_query_cache(access):
    from pilot import store
    base = store.workspace_dir(access.workspace)
    cache = base/"data/cache/query_embeddings"
    if not cache.exists(): return
    with store.db() as c:
        store.authorize(c,access)
        for path in cache.rglob("*.json"):
            if path.is_symlink() or not path.resolve().is_relative_to(cache.resolve()):
                raise CloudStorageError("Invalid query cache path.")
            put(c,access.workspace,None,path.relative_to(base).as_posix(),path.read_bytes())


def remove_objects(keys):
    for start in range(0,len(keys),100):
        try:
            storage().remove(keys[start:start+100])
        except Exception:
            raise CloudStorageError("Private file cleanup could not finish. Retry deletion when storage is available.") from None


def delete_document(access, document_id):
    from pilot import store
    with store.db() as c:
        store.document(c,access,document_id)
        # Include old versions/orphans from interrupted uploads, not just the manifest.
        prefix = store.uid(access.workspace)+"/"+store.uid(document_id)
        keys=[]
        try:
            offset=0
            while True:
                rows=storage().list(prefix,{"limit":1000,"offset":offset,"sortBy":{"column":"name","order":"asc"}})
                for row in rows:
                    if not re.fullmatch(r"[a-f0-9]{64}-[a-f0-9]{64}",row["name"]):
                        raise ValueError()
                    keys.append(prefix+"/"+row["name"])
                if len(rows)<1000: break
                offset+=1000
        except Exception:
            raise CloudStorageError("Private source versions could not be listed for deletion. Retry when storage is available.") from None
        remove_objects(keys)
        c.execute("DELETE FROM artifacts WHERE workspace=? AND document_id=?", (access.workspace,document_id))
    cached_download.cache_clear()
