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
import subprocess
import sys
import tempfile
import time

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/"app"))
from pilot import cloud_db, cloud_files

TABLES = ("workspaces","members","documents","jobs","decisions","decision_events","worker_status","model_connections")
PATH_FIELDS = {"pdf_path","image_path","page_image_path","source_path"}
RECORD_KEYS = {
    "workspaces":("id",), "members":("principal","workspace"), "documents":("id",),
    "jobs":("id",), "decisions":("job","atom"), "decision_events":("id",),
    "worker_status":("id",), "model_connections":("principal",),
}


class MigrationValidationError(cloud_db.CloudStorageError):
    """A fixed validation message authored in this module, safe to display."""


class VectorExportError(cloud_db.CloudStorageError):
    def __init__(self,operation,error=None,*,metadata=None):
        super().__init__("The copied index export failed.",code="migration_vector_export")
        self.details={"operation":operation}
        if error is not None:
            self.details["error_type"]=type(error).__name__
            for name in ("errno","winerror"):
                value=getattr(error,name,None)
                if isinstance(value,int): self.details[name]=value
        if metadata:
            for key in ("errno","winerror","exit_code"):
                if type(metadata.get(key)) is int: self.details[key]=metadata[key]
            name=metadata.get("error_type","")
            if isinstance(name,str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}",name):
                self.details["error_type"]=name


class RecordMismatch(cloud_db.CloudStorageError):
    """Only schema names/counts/types are exposed; never customer field values."""
    def __init__(self,table,source,copied,reason,fields=(),source_row=None,target_row=None):
        super().__init__("Migrated customer records did not match; the transaction was rolled back.",code="migration_records")
        self.details={"table":table,"source_count":len(source),"target_count":len(copied),"reason":reason,"fields":sorted(fields)}
        if source_row is not None and target_row is not None:
            def kind(value):
                return {type(None):"null",str:"text",int:"integer",float:"float",bool:"boolean",bytes:"bytes"}.get(type(value),"other")
            self.details["field_types"]={field:{"source":kind(source_row.get(field)),"target":kind(target_row.get(field))} for field in sorted(fields)}


def verify_records(target,rows,report=None):
    update=report or (lambda stage:None)
    for table in TABLES:
        update("verify_records."+table)
        source=rows[table]
        copied=[dict(row) for row in target.execute("SELECT * FROM "+table).fetchall()]
        if len(copied)!=len(source):
            raise RecordMismatch(table,source,copied,"row_count")
        keys=RECORD_KEYS[table]
        def indexed(records):
            return {tuple(row.get(key) for key in keys):row for row in records}
        expected,actual=indexed(source),indexed(copied)
        if len(expected)!=len(source) or len(actual)!=len(copied) or expected.keys()!=actual.keys():
            raise RecordMismatch(table,source,copied,"record_keys")
        for identity,left in expected.items():
            right=actual[identity]
            if left.keys()!=right.keys():
                raise RecordMismatch(table,source,copied,"column_names",left.keys()^right.keys())
            fields=[key for key in left if left[key]!=right[key]]
            if fields:
                raise RecordMismatch(table,source,copied,"field_values",fields,left,right)


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
            raise MigrationValidationError("A source JSON artifact is invalid. Inspect the stopped backup before migration.") from None
    return data


def _export_worker(request_path):
    """Internal child process: all Chroma native handles die with this process."""
    root=request_path.parent
    if (request_path.name!="request.json" or not root.name.startswith("verifier-index-export-")
            or request_path.is_symlink() or root.is_symlink() or root.is_junction()):
        return 2
    operation="read_request"
    try:
        request=json.loads(request_path.read_text(encoding="utf-8"))
        index=root/"chroma"
        if index.is_symlink() or index.is_junction(): raise ValueError()
        operation="open_copied_index"
        import chromadb
        from chromadb.config import Settings
        client=chromadb.PersistentClient(path=str(index),settings=Settings(anonymized_telemetry=False))
        operation="read_collections"
        collections=client.list_collections()
        found=any((getattr(c,"name",None) or str(c))==request["collection"] for c in collections)
        if not found and collections:
            (root/"result.json").write_text(json.dumps({"status":"incompatible_collection"}),encoding="utf-8")
            return 1
        operation="read_vectors"
        with (root/"vectors.jsonl").open("w",encoding="utf-8") as output:
            if found:
                collection=client.get_collection(request["collection"],embedding_function=None)
                count=collection.count()
                for document_id in request["documents"]:
                    for offset in range(0,count,200):
                        batch = collection.get(where={"document_id":document_id},include=["embeddings","documents","metadatas"],limit=200,offset=offset)
                        if not batch["ids"]: break
                        record={"document_id":document_id,"batch":{"ids":batch["ids"],"embeddings":[[float(x) for x in vector] for vector in batch["embeddings"]],
                                "documents":batch["documents"],"metadatas":batch["metadatas"]}}
                        output.write(json.dumps(record,allow_nan=False)+"\n")
        (root/"result.json").write_text(json.dumps({"status":"passed"}),encoding="utf-8")
        return 0
    except Exception as error:
        diagnostic=VectorExportError(operation,error).details
        (root/"result.json").write_text(json.dumps({"status":"failed",**diagnostic}),encoding="utf-8")
        return 1


def exported_vectors(index, collection_name, document_ids):
    """Only a child opens the disposable copy; it exits before Windows cleanup."""
    if not index.exists(): return
    if index.is_symlink() or index.is_junction():
        raise MigrationValidationError("Linked indexes are not allowed in a migration source.")
    operation="copy_index"
    try:
        with tempfile.TemporaryDirectory(prefix="verifier-index-export-") as temporary:
            root=Path(temporary).resolve()
            for path in index.rglob("*"):
                if path.is_symlink() or path.is_junction():
                    raise MigrationValidationError("Linked index files are not allowed in a migration source.")
            shutil.copytree(index,root/"chroma")
            request=root/"request.json"
            request.write_text(json.dumps({"collection":collection_name,"documents":list(document_ids)}),encoding="utf-8")
            operation="start_export_worker"
            result=subprocess.run([sys.executable,str(Path(__file__).resolve()),"--export-copy",str(request)],
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=300,check=False)
            operation="export_worker_result"
            if not (root/"result.json").is_file():
                raise VectorExportError(operation,metadata={"exit_code":result.returncode})
            status=json.loads((root/"result.json").read_text(encoding="utf-8"))
            if status.get("status")=="incompatible_collection":
                raise MigrationValidationError("A source index has incompatible embedding settings. No embeddings were regenerated.")
            if result.returncode!=0 or status.get("status")!="passed":
                child_operation=status.get("operation")
                if child_operation not in {"read_request","open_copied_index","read_collections","read_vectors"}:
                    child_operation=operation
                raise VectorExportError(child_operation,metadata={**status,"exit_code":result.returncode})
            operation="read_exported_batches"
            with (root/"vectors.jsonl").open(encoding="utf-8") as output:
                for line in output:
                    record=json.loads(line)
                    yield record["document_id"],record["batch"]
            operation="cleanup_copy"
    except cloud_db.CloudStorageError:
        raise
    except Exception as error:
        raise VectorExportError(operation,error) from None


def _validate_source(rows,source):
    from pilot.store import uid
    for workspace in rows["workspaces"]:
        uid(workspace["id"])
    if any(row["status"] == "running" for row in rows["jobs"]):
        raise MigrationValidationError("Source contains running jobs. Stop services and recover interrupted jobs before migration.")
    if any(time.time()-row["heartbeat"] < 20 for row in rows["worker_status"]):
        raise MigrationValidationError("The source worker heartbeat is recent. Stop both apps and the worker, then wait 20 seconds.")
    from cryptography.fernet import Fernet
    import os
    try:
        cipher = Fernet(os.environ["CUSTOMER_KEY_ENCRYPTION_KEY"].encode())
        for row in rows["model_connections"]:
            value = json.loads(cipher.decrypt(row["secret"].encode()))
            if value["principal"] != row["principal"] or value["version"] != row["version"]:
                raise ValueError()
    except Exception:
        raise MigrationValidationError("Configure the preserved master key matching the source connections. No data was migrated.") from None
    for doc in rows["documents"]:
        uid(doc["id"]); uid(doc["workspace"])
        pdf=source/"workspaces"/doc["workspace"]/"data/documents"/doc["id"]/"original.pdf"
        if not pdf.is_file() or hashlib.sha256(pdf.read_bytes()).hexdigest() != doc["sha"]:
            raise MigrationValidationError("A registered source PDF is missing or changed. Restore a consistent backup before migration.")


def migrate(source, *, services_stopped=False, report=None):
    update=report or (lambda stage:None)
    update("source_validation")
    if not services_stopped:
        raise MigrationValidationError("Migration requires --services-stopped and a verified cold backup.")
    source = Path(source).resolve()
    with closing(source_db(source)) as c:
        if c.execute("PRAGMA quick_check").fetchall()[0][0] != "ok":
            raise MigrationValidationError("Source database integrity check failed.")
        rows = {table:[dict(row) for row in c.execute("SELECT * FROM "+table)] for table in TABLES}
    _validate_source(rows,source)
    update("database_schema")
    cloud_db.initialize()
    update("private_bucket")
    cloud_files.storage()  # Verify bucket privacy before any database copy.
    import os
    from retrieval.cloud_vectors import vector_literal
    model=os.getenv("GEMINI_EMBEDDING_MODEL","gemini-embedding-001")
    dimensions=int(os.getenv("GEMINI_EMBEDDING_DIMENSIONS","768"))
    if dimensions != 768:
        raise MigrationValidationError("The cloud schema requires the original 768-dimensional embeddings.")
    collection_name="evidence_v2_"+hashlib.sha256(f"{model}:{dimensions}".encode()).hexdigest()[:12]
    vectors=files=0
    update("target_empty_check")
    with cloud_db.db() as target:
        target.execute("BEGIN IMMEDIATE")
        if target.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]:
            raise MigrationValidationError("The target customer schema is not empty. Migration will not overwrite it.")
        for table in TABLES:
            update("copy_records."+table)
            for row in rows[table]:
                columns=list(row)
                if not all(re.fullmatch(r"[a-z_]+",column) for column in columns):
                    raise MigrationValidationError("Unexpected source database columns. Migration was rolled back.")
                target.execute("INSERT INTO "+table+"("+",".join(columns)+") VALUES("+",".join("?" for _ in columns)+")",list(row.values()))
        # Fail before any file transfers if the control rows do not round-trip.
        verify_records(target,rows,update)
        for workspace in rows["workspaces"]:
            ws=workspace["id"]
            base=source/"workspaces"/ws
            docs=[doc for doc in rows["documents"] if doc["workspace"]==ws]
            update("upload_artifacts")
            for doc in docs:
                for path in cloud_files.document_files(base,doc["id"]):
                    cloud_files.put(target,ws,doc["id"],path.relative_to(base).as_posix(),artifact_bytes(path)); files+=1
            cache=base/"data/cache/query_embeddings"
            if cache.exists():
                for path in cache.rglob("*.json"):
                    if path.is_symlink() or not path.resolve().is_relative_to(cache.resolve()):
                        raise MigrationValidationError("Invalid source cache path.")
                    cloud_files.put(target,ws,None,path.relative_to(base).as_posix(),path.read_bytes()); files+=1
            target.execute("INSERT INTO vector_collections(workspace,name,model,dimensions) VALUES(?,?,?,?)",(ws,collection_name,model,dimensions))
            seen={doc["id"]:{} for doc in docs}
            update("export_vectors")
            for document_id,batch in exported_vectors(base/"data/chroma",collection_name,list(seen)):
                for cid,vector,text,meta in zip(batch["ids"],batch["embeddings"],batch["documents"],batch["metadatas"]):
                    if not isinstance(meta,dict) or meta.get("document_id") != document_id:
                        raise MigrationValidationError("A source vector belongs to a different document.")
                    target.execute("INSERT INTO vectors(workspace,collection,id,document_id,embedding,document,metadata) VALUES(?,?,?,?,?::extensions.vector,?,?)",
                                   (ws,collection_name,cid,document_id,vector_literal(vector,dimensions),text,json.dumps(meta,allow_nan=False)))
                    seen[document_id][cid]=meta.get("content_hash"); vectors+=1
            for doc in docs:
                if doc["status"] != "ready": continue
                chunks=json.loads((base/"data/processed"/doc["id"]/"chunks.json").read_text(encoding="utf-8"))["chunks"]
                expected={chunk["id"]:hashlib.sha256(chunk["text"].encode()).hexdigest() for chunk in chunks}
                if not expected or expected != seen[doc["id"]]:
                    raise MigrationValidationError("A ready document has incomplete or stale vectors. No cloud database rows were committed.")
        verify_records(target,rows,update)
        update("verify_private_artifacts")
        for artifact in target.execute("SELECT * FROM artifacts").fetchall():
            cloud_files.download(artifact)  # Verify private-object length and SHA before commit.
        target.execute("SELECT setval(pg_get_serial_sequence('verifier.decision_events','id'),COALESCE(MAX(id),1),COUNT(*)>0) FROM decision_events")
        update("database_commit")
    return {"mode":"copied_to_supabase","table_counts":{table:len(value) for table,value in rows.items()},
            "artifacts":files,"vectors":vectors,"embedding_provider_calls":0,"source_modified":False,
            "next":"Verify cloud login, documents, two-workspace isolation, cited review and retry before deployment."}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=PROJECT/"customer_data")
    parser.add_argument("--apply",action="store_true")
    parser.add_argument("--services-stopped",action="store_true")
    args=parser.parse_args(argv)
    stage="environment"
    def report(value):
        nonlocal stage
        stage=value
        print("Checking "+value+"...",flush=True)
    try:
        if args.apply:
            from dotenv import load_dotenv
            load_dotenv(PROJECT/".env",override=False)
        result=migrate(args.source,services_stopped=args.services_stopped,report=report) if args.apply else inventory(args.source)
        print(json.dumps(result,indent=2))
    except cloud_db.CloudStorageError as error:
        diagnostic={"status":"failed","stage":stage,**cloud_db.safe_diagnostic(error),"local_data_modified":False,"provider_calls":0}
        # Locally authored validation messages are fixed, safe text. Provider
        # errors use the controlled category above rather than raw exceptions.
        if isinstance(error,MigrationValidationError): diagnostic["message"]=str(error)
        if isinstance(error,(RecordMismatch,VectorExportError)): diagnostic["details"]=error.details
        print(json.dumps(diagnostic,indent=2),file=sys.stderr)
        return 1
    except ImportError:
        print(json.dumps({"status":"failed","stage":stage,"code":"environment_dependency","message":"Install the project dependencies, including psycopg[binary] and the pinned Chroma version, before applying migration.","local_data_modified":False,"provider_calls":0},indent=2),file=sys.stderr)
        return 1
    except Exception as error:
        print(json.dumps({"status":"failed","stage":stage,"code":"migration_unexpected","message":"Migration did not complete. Raw exception text is withheld to protect private data.","error_type":type(error).__name__,"local_data_modified":False,"provider_calls":0},indent=2),file=sys.stderr)
        return 1
    return 0


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="--export-copy":
        raise SystemExit(_export_worker(Path(sys.argv[2])))
    raise SystemExit(main())
