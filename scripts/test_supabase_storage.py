"""Opt-in real cloud smoke with disposable workspaces, never model-provider calls."""
from __future__ import annotations

import argparse
import hashlib
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import sys
import time
import uuid

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/"app"))
from pilot import cloud_db, cloud_files, store
from retrieval.cloud_vectors import Collection


def check_configuration(report=None):
    update=report or (lambda stage:None)
    update("database_schema")
    cloud_db.initialize()
    with cloud_db.db() as c:
        row=c.execute("SELECT COUNT(*) AS table_count,BOOL_AND(rowsecurity) AS rls_enabled FROM pg_tables WHERE schemaname='verifier'").fetchone()
        grants=c.execute("SELECT has_schema_privilege('anon','verifier','USAGE') AS anon_access,has_schema_privilege('authenticated','verifier','USAGE') AS authenticated_access").fetchone()
        if row["table_count"]!=12 or not row["rls_enabled"] or grants["anon_access"] or grants["authenticated_access"]:
            raise cloud_db.CloudStorageError("Customer schema protection differs from the supplied setup.",code="schema_protection")
    update("private_bucket")
    cloud_files.storage()
    return {"passed":["Database schema version 1, 12 RLS-protected tables and private schema grants","Private Storage bucket available"],
            "provider_calls":0,"customer_data_used":False,"cloud_fixture_writes":False}


class SmokeFailure(Exception):
    def __init__(self,error,passed,cleanup,workspaces):
        self.error,self.passed,self.cleanup,self.workspaces=error,passed,cleanup,workspaces


def smoke(report=None):
    update=report or (lambda stage:None)
    check_configuration(update)
    workspaces=[uuid.uuid4().hex for _ in range(2)]
    principals=[hashlib.sha256(uuid.uuid4().bytes).hexdigest() for _ in range(2)]
    docs=[uuid.uuid4().hex for _ in range(2)]
    jobs=[uuid.uuid4().hex for _ in range(2)]
    tokens=[uuid.uuid4().hex for _ in range(2)]
    access=[store.Access(p,w,time.time()+600) for p,w in zip(principals,workspaces)]
    data=b"Private cloud storage fixture. No customer document."
    passed=[]
    previous={k:os.environ.get(k) for k in ("CUSTOMER_WORKSPACE_ID","CUSTOMER_JOB_ID","CUSTOMER_JOB_TOKEN")}
    cleanup_errors=[]
    failure=None
    fixtures_started=False
    try:
        update("worker_lock")
        lock=cloud_db.WorkerLock()
        try:
            lock.check()
            try: second=cloud_db.WorkerLock()
            except cloud_db.CloudStorageError as error:
                if error.code!="worker_active": raise
            else:
                second.close()
                raise AssertionError("Second worker acquired the same database lock")
        finally: lock.close()
        passed.append("Exclusive worker lock and session identity verified")
        update("fixture_database")
        with cloud_db.db() as c:
            fixtures_started=True
            for who,ws,doc,job,token in zip(principals,workspaces,docs,jobs,tokens):
                c.execute("INSERT INTO workspaces VALUES(?,?)",(ws,"Disposable storage test"))
                c.execute("INSERT INTO members VALUES(?,?)",(who,ws))
                c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",(doc,ws,"fixture.pdf",hashlib.sha256(data).hexdigest(),"ready",time.time()))
                c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,result,created,updated,run_token) VALUES(?,?,?,'verify','{}','running','Fixture','{}',?,?,?)",(job,ws,who,time.time(),time.time(),token))
        # Keep fixture identities available for authenticated cleanup even if an
        # object upload succeeds but its later manifest transaction fails.
        update("private_object_upload")
        with cloud_db.db() as c:
            for ws,doc in zip(workspaces,docs):
                cloud_files.put(c,ws,doc,f"data/documents/{doc}/original.pdf",data)
        update("private_object_download")
        for current,doc in zip(access,docs):
            assert store.document_bytes(current,doc)==data
        passed.append("Private object length/SHA roundtrip for two workspaces")
        update("workspace_isolation")
        for current,foreign_doc,foreign_job in ((access[0],docs[1],jobs[1]),(access[1],docs[0],jobs[0])):
            for action in (lambda:store.document_bytes(current,foreign_doc),lambda:store.get_job(current,foreign_job)):
                try: action()
                except PermissionError: pass
                else: raise AssertionError("Workspace isolation failed")
        passed.append("Foreign document and review denied in both directions")
        update("vector_roundtrip")
        for i in range(2):
            os.environ.update(CUSTOMER_WORKSPACE_ID=workspaces[i],CUSTOMER_JOB_ID=jobs[i],CUSTOMER_JOB_TOKEN=tokens[i])
            collection=Collection("fixture_storage_check","fixture_vectors",768)
            vector=[0.0]*768; vector[i]=1.0
            collection.upsert(ids=["same-chunk-id"],embeddings=[vector],documents=["Fixture "+str(i)],metadatas=[{"document_id":docs[i]}])
            hits=collection.query(query_embeddings=[vector],where={"document_id":docs[i]},n_results=2,include=["documents"])
            assert hits["documents"]==[["Fixture "+str(i)]]
            assert collection.get(where={"document_id":docs[1-i]},include=[])["ids"]==[]
        passed.append("Real pgvector insert/cosine search and overlapping-ID isolation")
        update("stale_token_rejection")
        with cloud_db.db() as c:
            c.execute("UPDATE jobs SET status='paused',run_token=NULL WHERE id=?",(jobs[1],))
        try:
            collection.delete(ids=["same-chunk-id"])
        except cloud_db.CloudStorageError as error:
            if error.code!="job_ownership_changed": raise
        else: raise AssertionError("Stale worker was allowed to change vectors")
        passed.append("Stale job token cannot modify vectors")
        update("public_download_denial")
        import httpx
        with cloud_db.db() as c:
            row=c.execute("SELECT object_key FROM artifacts WHERE workspace=?",(workspaces[0],)).fetchone()
        url=os.environ["SUPABASE_URL"].rstrip("/")+"/storage/v1/object/public/"+cloud_files.BUCKET+"/"+row["object_key"]
        response=httpx.get(url,timeout=15,follow_redirects=False)
        assert response.status_code in {400,401,403,404}
        passed.append("Private fixture object is not publicly downloadable")
    except Exception as error:
        failure=error
    finally:
        if failure is None: update("fixture_cleanup")
        for current,doc in (zip(access,docs) if fixtures_started else []):
            try:
                with cloud_db.db() as c:
                    exists=c.execute("SELECT 1 FROM documents WHERE workspace=? AND id=?",(current.workspace,doc)).fetchone()
                if exists: cloud_files.delete_document(current,doc)
            except Exception as error: cleanup_errors.append({"resource":"private_objects",**cloud_db.safe_diagnostic(error)})
        try:
            # Retain fixture identities if object cleanup failed so the operator
            # can retry authenticated cleanup using the reported workspace IDs.
            if fixtures_started and not cleanup_errors:
                with cloud_db.db() as c:
                    for ws in workspaces:
                        c.execute("DELETE FROM jobs WHERE workspace=?",(ws,))
                        c.execute("DELETE FROM documents WHERE workspace=?",(ws,))
                        c.execute("DELETE FROM vector_collections WHERE workspace=?",(ws,))
                        c.execute("DELETE FROM members WHERE workspace=?",(ws,))
                        c.execute("DELETE FROM workspaces WHERE id=?",(ws,))
        except Exception as error: cleanup_errors.append({"resource":"fixture_database_rows",**cloud_db.safe_diagnostic(error)})
        for key,value in previous.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
    if failure is not None or cleanup_errors:
        raise SmokeFailure(failure or cloud_db.CloudStorageError("Fixture cleanup needs operator attention.",code="fixture_cleanup"),passed,cleanup_errors,workspaces)
    return {"passed":passed,"provider_calls":0,"customer_data_used":False,"fixture_cleanup":"complete"}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    operation=parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--allow-cloud-write",action="store_true")
    operation.add_argument("--check-only",action="store_true",help="Read schema/bucket metadata only, without fixture writes. Local mode can stay enabled.")
    args=parser.parse_args(argv)
    stage="environment"
    def report(value):
        nonlocal stage
        stage=value
        print("Checking "+value+"...",flush=True)
    try:
        try:
            from dotenv import load_dotenv
        except ImportError:
            raise cloud_db.CloudStorageError("The project environment dependencies are missing.",code="environment_dependency") from None
        load_dotenv(PROJECT/".env",override=False)
        if not args.check_only and not cloud_db.enabled():
            raise cloud_db.CloudStorageError("Set CUSTOMER_STORAGE=supabase for the full fixture smoke.",code="storage_mode")
        result=check_configuration(report) if args.check_only else smoke(report)
        print(json.dumps({"status":"passed",**result},indent=2))
    except Exception as error:
        failure=error.error if isinstance(error,SmokeFailure) else error
        diagnostic={"status":"failed","stage":stage,**cloud_db.safe_diagnostic(failure),"provider_calls":0}
        if isinstance(error,SmokeFailure):
            diagnostic["passed"]=error.passed
            diagnostic["cleanup_required"]=error.cleanup
            if error.cleanup: diagnostic["fixture_workspaces"]=error.workspaces
        packages={}
        for name in ("psycopg","supabase","storage3","httpx","python-dotenv"):
            try: packages[name]=version(name)
            except PackageNotFoundError: packages[name]="not installed"
        diagnostic["package_versions"]=packages
        diagnostic["python_version"]=".".join(map(str,sys.version_info[:3]))
        print(json.dumps(diagnostic,indent=2),file=sys.stderr)
        return 1
    return 0


if __name__=="__main__": raise SystemExit(main())
