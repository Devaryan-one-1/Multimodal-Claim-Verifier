"""Explicit cold migration. Default is a read-only local inventory, no network."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import re
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/"app"))
from pilot import cloud_db, cloud_files

TABLES = ("workspaces","members","documents","jobs","decisions","decision_events","worker_status","model_connections")
PATH_FIELDS = {"pdf_path","image_path","page_image_path","source_path"}


def source_db(source):
    connection = sqlite3.connect((source/"control.sqlite3").resolve().as_uri()+"?mode=ro",uri=True,timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def inventory(source):
    source = Path(source).resolve()
    with closing(source_db(source)) as c:
        counts = {table:c.execute("SELECT COUNT(*) FROM "+table).fetchone()[0] for table in TABLES}
    total = sum(p.stat().st_size for p in source.rglob("*") if p.is_file() and not p.is_symlink())
    return {"mode":"inventory_only", "bytes":total,"table_counts":counts,"network_calls":0,"source_modified":False}


def portable_paths(value, key=""):
    if isinstance(value,dict):
        return {k:portable_paths(v,k) for k,v in value.items()}
    if isinstance(value,list):
        return [portable_paths(v,key) for v in value]
    if isinstance(value,str) and key in PATH_FIELDS:
        normalized = value.replace("\\","/")
        if normalized.startswith("data/"):
            cloud_files.relative_path(normalized)
            return normalized
        if "/data/" in normalized:
            relative = "data/"+normalized.split("/data/",1)[1]
            cloud_files.relative_path(relative)
            return relative
    return value


def artifact_bytes(path):
    data = path.read_bytes()
    if path.suffix == ".json":
        try:
            original = json.loads(data)
            portable = portable_paths(original)
            if portable != original:
                return json.dumps(portable,ensure_ascii=False).encode("utf-8")
        except (ValueError,UnicodeError):
            # An invalid extracted artifact is not silently accepted for migration.
            raise cloud_db.CloudStorageError("A source JSON artifact is invalid. Inspect the stopped backup before migration.") from None
    return data


def exported_vectors(index, collection_name, document_ids):
    """Chroma may write on open: only initialize it against a disposable copy."""
    if not index.exists(): return
    if index.is_symlink() or index.is_junction():
        raise cloud_db.CloudStorageError("Linked indexes are not allowed in a migration source.")
    with tempfile.TemporaryDirectory(prefix="verifier-index-export-") as temporary:
        copied = Path(temporary)/"chroma"
        for path in index.rglob("*"):
            if path.is_symlink() or path.is_junction():
                raise cloud_db.CloudStorageError("Linked index files are not allowed in a migration source.")
        shutil.copytree(index,copied)
        import chromadb
        from chromadb.config import Settings
        client = chromadb.PersistentClient(path=str(copied),settings=Settings(anonymized_telemetry=False))
        try:
            collections = client.list_collections()
            if not any((getattr(c,"name",None) or str(c)) == collection_name for c in collections):
                if collections:
                    raise cloud_db.CloudStorageError("A source index has incompatible embedding settings. No embeddings were regenerated.")
                return
            collection = client.get_collection(collection_name)
            for document_id in document_ids:
                for offset in range(0,collection.count(),200):
                    batch = collection.get(where={"document_id":document_id},include=["embeddings","documents","metadatas"],limit=200,offset=offset)
                    if not batch["ids"]: break
                    yield document_id, {"ids":batch["ids"],"embeddings":[[float(x) for x in vector] for vector in batch["embeddings"]],
                                        "documents":batch["documents"],"metadatas":batch["metadatas"]}
        finally:
            # Release native index handles before Windows removes the copied directory.
            client._system.stop()


def _validate_source(rows,source):
    from pilot.store import uid
    for workspace in rows["workspaces"]:
        uid(workspace["id"])
    if any(row["status"] == "running" for row in rows["jobs"]):
        raise cloud_db.CloudStorageError("Source contains running jobs. Stop services and recover interrupted jobs before migration.")
    if any(time.time()-row["heartbeat"] < 20 for row in rows["worker_status"]):
        raise cloud_db.CloudStorageError("The source worker heartbeat is recent. Stop both apps and the worker, then wait 20 seconds.")
    from cryptography.fernet import Fernet
    import os
    try:
        cipher = Fernet(os.environ["CUSTOMER_KEY_ENCRYPTION_KEY"].encode())
        for row in rows["model_connections"]:
            value = json.loads(cipher.decrypt(row["secret"].encode()))
            if value["principal"] != row["principal"] or value["version"] != row["version"]:
                raise ValueError()
    except Exception:
        raise cloud_db.CloudStorageError("Configure the preserved master key matching the source connections. No data was migrated.") from None
    for doc in rows["documents"]:
        uid(doc["id"]); uid(doc["workspace"])
        pdf=source/"workspaces"/doc["workspace"]/"data/documents"/doc["id"]/"original.pdf"
        if not pdf.is_file() or hashlib.sha256(pdf.read_bytes()).hexdigest() != doc["sha"]:
            raise cloud_db.CloudStorageError("A registered source PDF is missing or changed. Restore a consistent backup before migration.")


def migrate(source, *, services_stopped=False):
    if not services_stopped:
        raise cloud_db.CloudStorageError("Migration requires --services-stopped and a verified cold backup.")
    source = Path(source).resolve()
    with closing(source_db(source)) as c:
        if c.execute("PRAGMA quick_check").fetchall()[0][0] != "ok":
            raise cloud_db.CloudStorageError("Source database integrity check failed.")
        rows = {table:[dict(row) for row in c.execute("SELECT * FROM "+table)] for table in TABLES}
    _validate_source(rows,source)
    cloud_db.initialize()
    cloud_files.storage()  # Verify bucket privacy before any database copy.
    import os
    from retrieval.cloud_vectors import vector_literal
    model=os.getenv("GEMINI_EMBEDDING_MODEL","gemini-embedding-001")
    dimensions=int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS","768"))
    if dimensions != 768:
        raise cloud_db.CloudStorageError("The cloud schema requires the original 768-dimensional embeddings.")
    collection_name="evidence_v2_"+hashlib.sha256(f"{model}:{dimensions}".encode()).hexdigest()[:12]
    vectors=files=0
    with cloud_db.db() as target:
        target.execute("BEGIN IMMEDIATE")
        if target.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]:
            raise cloud_db.CloudStorageError("The target customer schema is not empty. Migration will not overwrite it.")
        for table in TABLES:
            for row in rows[table]:
                columns=list(row)
                if not all(re.fullmatch(r"[a-z_]+",column) for column in columns):
                    raise cloud_db.CloudStorageError("Unexpected source database columns. Migration was rolled back.")
                target.execute("INSERT INTO "+table+"("+",".join(columns)+") VALUES("+",".join("?" for _ in columns)+")",list(row.values()))
        for workspace in rows["workspaces"]:
            ws=workspace["id"]
            base=source/"workspaces"/ws
            docs=[doc for doc in rows["documents"] if doc["workspace"]==ws]
            for doc in docs:
                for path in cloud_files.document_files(base,doc["id"]):
                    cloud_files.put(target,ws,doc["id"],path.relative_to(base).as_posix(),artifact_bytes(path)); files+=1
            cache=base/"data/cache/query_embeddings"
            if cache.exists():
                for path in cache.rglob("*.json"):
                    if path.is_symlink() or not path.resolve().is_relative_to(cache.resolve()):
                        raise cloud_db.CloudStorageError("Invalid source cache path.")
                    cloud_files.put(target,ws,None,path.relative_to(base).as_posix(),path.read_bytes()); files+=1
            target.execute("INSERT INTO vector_collections(workspace,name,model,dimensions) VALUES(?,?,?,?)",(ws,collection_name,model,dimensions))
            seen={doc["id"]:{} for doc in docs}
            for document_id,batch in exported_vectors(base/"data/chroma",collection_name,list(seen)):
                for cid,vector,text,meta in zip(batch["ids"],batch["embeddings"],batch["documents"],batch["metadatas"]):
                    if not isinstance(meta,dict) or meta.get("document_id") != document_id:
                        raise cloud_db.CloudStorageError("A source vector belongs to a different document.")
                    target.execute("INSERT INTO vectors(workspace,collection,id,document_id,embedding,document,metadata) VALUES(?,?,?,?,?::extensions.vector,?,?)",
                                   (ws,collection_name,cid,document_id,vector_literal(vector,dimensions),text,json.dumps(meta,allow_nan=False)))
                    seen[document_id][cid]=meta.get("content_hash"); vectors+=1
            for doc in docs:
                if doc["status"] != "ready": continue
                chunks=json.loads((base/"data/processed"/doc["id"]/"chunks.json").read_text(encoding="utf-8"))["chunks"]
                expected={chunk["id"]:hashlib.sha256(chunk["text"].encode()).hexdigest() for chunk in chunks}
                if not expected or expected != seen[doc["id"]]:
                    raise cloud_db.CloudStorageError("A ready document has incomplete or stale vectors. No cloud database rows were committed.")
        for table in TABLES:
            copied=[dict(row) for row in target.execute("SELECT * FROM "+table).fetchall()]
            order=lambda row:str(tuple(row.get(key) for key in ("id","principal","workspace","job","atom") if key in row))
            if sorted(copied,key=order) != sorted(rows[table],key=order):
                raise cloud_db.CloudStorageError("Migrated customer records did not match; the transaction was rolled back.")
        for artifact in target.execute("SELECT * FROM artifacts").fetchall():
            cloud_files.download(artifact)  # Verify private-object length and SHA before commit.
        target.execute("SELECT setval(pg_get_serial_sequence('verifier.decision_events','id'),COALESCE(MAX(id),1),COUNT(*)>0) FROM decision_events")
    return {"mode":"copied_to_supabase","table_counts":{table:len(value) for table,value in rows.items()},
            "artifacts":files,"vectors":vectors,"embedding_provider_calls":0,"source_modified":False,
            "next":"Verify cloud login, documents, two-workspace isolation, cited review and retry before deployment."}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=PROJECT/"customer_data")
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--services-stopped",action="store_true")
    args=parser.parse_args(argv)
    try:
        if args.apply:
            from dotenv import load_dotenv
            load_dotenv(PROJECT/".env",override=False)
        result=migrate(args.source,services_stopped=args.services_stopped) if args.apply else inventory(args.source)
        print(json.dumps(result,indent=2))
    except cloud_db.CloudStorageError as error:
        print("Migration stopped: "+str(error),file=sys.stderr)
        return 1
    except ImportError:
        print("Install the project dependencies, including psycopg[binary] and the pinned Chroma version, before applying migration.",file=sys.stderr)
        return 1
    except Exception:
        print("Migration did not complete. Check the stopped source backup, preserved key, empty target and cloud configuration. No local data was deleted.",file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    raise SystemExit(main())
