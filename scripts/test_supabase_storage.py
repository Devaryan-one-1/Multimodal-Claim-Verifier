"""Opt-in real cloud smoke with disposable workspaces, never model-provider calls."""
from __future__ import annotations

import argparse
import hashlib
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


def smoke():
    cloud_db.initialize()
    cloud_files.storage()
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
    try:
        lock=cloud_db.WorkerLock()
        try:
            lock.check()
            try: second=cloud_db.WorkerLock()
            except cloud_db.CloudStorageError: pass
            else:
                second.close()
                raise AssertionError("Second worker acquired the same database lock")
        finally: lock.close()
        passed.append("Exclusive worker lock and session identity verified")
        with cloud_db.db() as c:
            for who,ws,doc,job,token in zip(principals,workspaces,docs,jobs,tokens):
                c.execute("INSERT INTO workspaces VALUES(?,?)",(ws,"Disposable storage test"))
                c.execute("INSERT INTO members VALUES(?,?)",(who,ws))
                c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",(doc,ws,"fixture.pdf",hashlib.sha256(data).hexdigest(),"ready",time.time()))
                c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,result,created,updated,run_token) VALUES(?,?,?,'verify','{}','running','Fixture','{}',?,?,?)",(job,ws,who,time.time(),time.time(),token))
                cloud_files.put(c,ws,doc,f"data/documents/{doc}/original.pdf",data)
        for current,doc in zip(access,docs):
            assert store.document_bytes(current,doc)==data
        passed.append("Private object length/SHA roundtrip for two workspaces")
        for current,foreign_doc,foreign_job in ((access[0],docs[1],jobs[1]),(access[1],docs[0],jobs[0])):
            for action in (lambda:store.document_bytes(current,foreign_doc),lambda:store.get_job(current,foreign_job)):
                try: action()
                except PermissionError: pass
                else: raise AssertionError("Workspace isolation failed")
        passed.append("Foreign document and review denied in both directions")
        for i in range(2):
            os.environ.update(CUSTOMER_WORKSPACE_ID=workspaces[i],CUSTOMER_JOB_ID=jobs[i],CUSTOMER_JOB_TOKEN=tokens[i])
            collection=Collection("fixture_storage_check","fixture_vectors",768)
            vector=[0.0]*768; vector[i]=1.0
            collection.upsert(ids=["same-chunk-id"],embeddings=[vector],documents=["Fixture "+str(i)],metadatas=[{"document_id":docs[i]}])
            hits=collection.query(query_embeddings=[vector],where={"document_id":docs[i]},n_results=2,include=["documents"])
            assert hits["documents"]==[["Fixture "+str(i)]]
            assert collection.get(where={"document_id":docs[1-i]},include=[])["ids"]==[]
        passed.append("Real pgvector insert/cosine search and overlapping-ID isolation")
        with cloud_db.db() as c:
            c.execute("UPDATE jobs SET status='paused',run_token=NULL WHERE id=?",(jobs[1],))
        try:
            collection.delete(ids=["same-chunk-id"])
        except cloud_db.CloudStorageError: pass
        else: raise AssertionError("Stale worker was allowed to change vectors")
        passed.append("Stale job token cannot modify vectors")
        import httpx
        with cloud_db.db() as c:
            row=c.execute("SELECT object_key FROM artifacts WHERE workspace=?",(workspaces[0],)).fetchone()
        url=os.environ["SUPABASE_URL"].rstrip("/")+"/storage/v1/object/public/"+cloud_files.BUCKET+"/"+row["object_key"]
        response=httpx.get(url,timeout=15,follow_redirects=False)
        assert response.status_code in {400,401,403,404}
        passed.append("Private fixture object is not publicly downloadable")
    finally:
        for current,doc in zip(access,docs):
            try:
                with cloud_db.db() as c:
                    exists=c.execute("SELECT 1 FROM documents WHERE workspace=? AND id=?",(current.workspace,doc)).fetchone()
                if exists: cloud_files.delete_document(current,doc)
            except Exception: cleanup_errors.append("private objects")
        try:
            with cloud_db.db() as c:
                for ws in workspaces:
                    c.execute("DELETE FROM jobs WHERE workspace=?",(ws,))
                    c.execute("DELETE FROM documents WHERE workspace=?",(ws,))
                    c.execute("DELETE FROM vector_collections WHERE workspace=?",(ws,))
                    c.execute("DELETE FROM members WHERE workspace=?",(ws,))
                    c.execute("DELETE FROM workspaces WHERE id=?",(ws,))
        except Exception: cleanup_errors.append("fixture database rows")
        for key,value in previous.items():
            if value is None: os.environ.pop(key,None)
            else: os.environ[key]=value
        if cleanup_errors:
            print(json.dumps({"cleanup_required":cleanup_errors,"fixture_workspaces":workspaces}),file=sys.stderr)
    if cleanup_errors:
        raise cloud_db.CloudStorageError("Fixture cleanup needs operator attention; do not count the smoke as passed.")
    return {"passed":passed,"provider_calls":0,"customer_data_used":False,"fixture_cleanup":"complete"}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-cloud-write",action="store_true",required=True)
    parser.parse_args(argv)
    from dotenv import load_dotenv
    load_dotenv(PROJECT/".env",override=False)
    if not cloud_db.enabled():
        print("Set CUSTOMER_STORAGE=supabase with the private server configuration first.",file=sys.stderr)
        return 1
    try:
        print(json.dumps(smoke(),indent=2))
    except Exception:
        print("Cloud storage smoke did not pass. Check schema, private storage, session connection, quotas and fixture cleanup. No model calls were made.",file=sys.stderr)
        return 1
    return 0


if __name__=="__main__": raise SystemExit(main())
