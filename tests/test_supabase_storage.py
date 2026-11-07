"""Cloud contracts with temporary state; no Supabase, Google or OpenRouter calls."""
from contextlib import contextmanager, ExitStack, closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch

PROJECT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(PROJECT/"app"))
sys.path.insert(0,str(PROJECT))
from pilot import store, cloud_db, cloud_files
from retrieval.cloud_vectors import Collection, vector_literal
from scripts import migrate_supabase as migration

W1,W2,D1,D2,J1,TOKEN=(char*32 for char in "abcdef")


class DatabaseAdapterTests(unittest.TestCase):
    def test_local_is_default_and_unknown_mode_fails(self):
        with patch.dict(os.environ,{},clear=True):
            self.assertFalse(cloud_db.enabled())
            with patch.dict(os.environ,{"CUSTOMER_STORAGE":"unexpected"}):
                self.assertRaises(cloud_db.CloudStorageError,cloud_db.enabled)

    def test_connection_rejects_non_tls_and_transaction_pooling_without_using_secret(self):
        fake=MagicMock()
        for url in ("postgresql://user:PRIVATE@db:6543/postgres", "postgresql://user:PRIVATE@db/postgres?sslmode=disable", "https://user:PRIVATE@db"):
            with self.subTest(url=url),patch.dict(os.environ,{"CUSTOMER_DATABASE_URL":url}),patch.dict(sys.modules,{"psycopg":fake}):
                with self.assertRaises(cloud_db.CloudStorageError) as error:
                    cloud_db.connect()
                self.assertNotIn("PRIVATE",str(error.exception))
        fake.connect.assert_not_called()

    def test_connection_uses_tls_no_prepared_statements_and_private_schema(self):
        fake=MagicMock()
        with patch.dict(os.environ,{"CUSTOMER_DATABASE_URL":"postgresql://user:PRIVATE@db:5432/postgres"}),patch.dict(sys.modules,{"psycopg":fake}):
            cloud_db.connect()
        self.assertEqual(fake.connect.call_args.kwargs["sslmode"],"require")
        self.assertIsNone(fake.connect.call_args.kwargs["prepare_threshold"])
        self.assertTrue(any("SET search_path=verifier" in call.args[0] for call in fake.connect.return_value.execute.call_args_list))
        self.assertFalse(fake.connect.return_value.autocommit)

    def test_store_sql_is_bound_and_rows_support_both_existing_access_styles(self):
        native=MagicMock()
        native.execute.return_value.description=[types.SimpleNamespace(name="count")]
        native.execute.return_value.fetchone.return_value=(7,)
        c=cloud_db.Connection(native)
        row=c.execute("SELECT '?' AS literal,count(*) AS count FROM jobs WHERE workspace=?",("bound'value",)).fetchone()
        self.assertEqual(row["count"],7); self.assertEqual(row[0],7); self.assertEqual(dict(row),{"count":7})
        self.assertEqual(native.execute.call_args.args,("SELECT '?' AS literal,count(*) AS count FROM jobs WHERE workspace=%s",("bound'value",)))
        self.assertEqual(cloud_db.placeholders("SELECT '10%' WHERE note=?"),"SELECT '10%%' WHERE note=%s")
        c.execute("BEGIN IMMEDIATE")
        self.assertIn("pg_advisory_xact_lock",native.execute.call_args.args[0])
        c.execute("INSERT OR IGNORE INTO workspaces VALUES(?,?)",(W1,"Workspace"))
        self.assertTrue(native.execute.call_args.args[0].endswith("ON CONFLICT DO NOTHING"))

    def test_failed_transaction_rolls_back_closes_and_redacts_database_errors(self):
        native=MagicMock()
        error=type("DatabaseError",(Exception,),{"__module__":"psycopg.errors"})
        with patch.object(cloud_db,"connect",return_value=native):
            with self.assertRaises(cloud_db.CloudStorageError) as raised:
                with cloud_db.db():
                    raise error("PRIVATE_DATABASE_BODY")
        native.rollback.assert_called_once(); native.close.assert_called_once(); native.commit.assert_not_called()
        self.assertNotIn("PRIVATE",str(raised.exception))

    def test_worker_lock_refuses_second_worker_and_detects_lost_session(self):
        native=MagicMock()
        native.execute.return_value.fetchone.return_value=(False,123)
        with patch.object(cloud_db,"connect",return_value=native):
            self.assertRaises(cloud_db.CloudStorageError,cloud_db.WorkerLock)
        native.close.assert_called_once()
        native.reset_mock(); native.execute.return_value.fetchone.return_value=(True,123)
        with patch.object(cloud_db,"connect",return_value=native):
            lock=cloud_db.WorkerLock()
        native.execute.side_effect=RuntimeError("PRIVATE")
        with self.assertRaises(cloud_db.CloudStorageError) as error:
            lock.check()
        self.assertNotIn("PRIVATE",str(error.exception)); lock.close()

    def test_private_storage_rejects_public_bucket_or_browser_key(self):
        fake=MagicMock(); http=MagicMock()
        cloud_files.storage.cache_clear()
        self.addCleanup(cloud_files.storage.cache_clear)
        with patch.dict(os.environ,{"SUPABASE_URL":"https://fixture.supabase.co","SUPABASE_SECRET_KEY":"sb_publishable_fixture"}):
            self.assertRaises(cloud_db.CloudStorageError,cloud_files.storage)
        with patch.dict(os.environ,{"SUPABASE_URL":"https://fixture.supabase.co","SUPABASE_SECRET_KEY":"sb_secret_fixture"}),patch.dict(sys.modules,{"storage3":fake,"httpx":http}):
            fake.SyncStorageClient.return_value.get_bucket.return_value={"public":True}
            self.assertRaises(cloud_db.CloudStorageError,cloud_files.storage)
            fake.SyncStorageClient.return_value.get_bucket.return_value={"public":False}
            cloud_files.storage()
            headers=fake.SyncStorageClient.call_args.args[1]
            self.assertEqual(headers["apikey"],"sb_secret_fixture")
            self.assertNotIn("Authorization",headers)

    def test_cloud_failure_is_operational_and_provider_body_is_not_displayed(self):
        from services.failure_details import describe_failure
        details=describe_failure(cloud_db.CloudStorageError("PRIVATE_DATABASE_BODY"),calls=[{"status":"error","http_status":429}])
        self.assertEqual(details["code"],"cloud_storage")
        self.assertNotIn("PRIVATE",json.dumps(details))
        self.assertNotIn("http_status",details)


class FakeBucket:
    def __init__(self): self.objects={}; self.downloads=0; self.uploads=0
    def upload(self,key,data,options): self.objects[key]=data; self.uploads+=1
    def download(self,key): self.downloads+=1; return self.objects[key]
    def remove(self,keys):
        for key in keys: self.objects.pop(key,None)
    def list(self,prefix,options):
        names=sorted(k[len(prefix)+1:] for k in self.objects if k.startswith(prefix+"/"))
        return [{"name":n} for n in names[options["offset"]:options["offset"]+options["limit"]]]


class SQLiteCloudConnection:
    """Exercise actual store/file/vector methods; adapt only PostgreSQL syntax."""
    def __init__(self,c): self.c=c
    def execute(self,sql,args=()):
        sql=sql.replace("embedding <=> ?::extensions.vector","cosine_distance(embedding, ?)")
        sql=sql.replace("?::extensions.vector","?").replace("embedding::text","embedding").replace(" FOR UPDATE OF j","")
        if sql.startswith("SELECT setval("): return self.c.execute("SELECT 1")
        return self.c.execute(sql,args)


class CloudStorageTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.database=self.temp/"control.sqlite3"
        self.stack.enter_context(patch.dict(os.environ,{"CUSTOMER_STORAGE":"local","CUSTOMER_DATA_ROOT":str(self.temp)}))
        store.initialize()
        with closing(sqlite3.connect(self.database)) as c:
            c.executescript('''
CREATE TABLE schema_version(id integer primary key,version integer); INSERT INTO schema_version VALUES(1,1);
CREATE TABLE artifacts(workspace text,document_id text,path text,object_key text,sha text,size integer,PRIMARY KEY(workspace,path));
CREATE TABLE vector_collections(workspace text,name text,model text,dimensions integer,PRIMARY KEY(workspace,name));
CREATE TABLE vectors(workspace text,collection text,id text,document_id text,embedding text,document text,metadata text,PRIMARY KEY(workspace,collection,id));
''')
            for ws,person,doc in ((W1,"alice",D1),(W2,"bob",D2)):
                c.execute("INSERT INTO workspaces VALUES(?,?)",(ws,person))
                c.execute("INSERT INTO members VALUES(?,?)",(person,ws))
                c.execute("INSERT INTO documents(id,workspace,name,sha,status,created) VALUES(?,?,?,?,?,?)",(doc,ws,"report.pdf",hashlib.sha256(b"pdf").hexdigest(),"ready",time.time()))
            c.execute("INSERT INTO jobs(id,workspace,principal,kind,payload,status,stage,result,created,updated,run_token) VALUES(?,?,?,'process','{}','running','Working','{}',?,?,?)",(J1,W1,"alice",time.time(),time.time(),TOKEN))
            c.commit()
        self.stack.enter_context(patch.dict(os.environ,{"CUSTOMER_STORAGE":"supabase","CUSTOMER_CACHE_ROOT":str(self.temp/"disposable"),"CUSTOMER_WORKSPACE_ID":W1,"CUSTOMER_JOB_ID":J1,"CUSTOMER_JOB_TOKEN":TOKEN}))
        self.a,self.b=store.Access("alice",W1,time.time()+300),store.Access("bob",W2,time.time()+300)
        self.stack.enter_context(patch.object(cloud_db,"db",self.db))
        self.bucket=FakeBucket()
        self.stack.enter_context(patch.object(cloud_files,"storage",return_value=self.bucket))
        cloud_files.cached_download.cache_clear()
        self.addCleanup(cloud_files.cached_download.cache_clear)

    @contextmanager
    def db(self):
        c=sqlite3.connect(self.database); c.row_factory=sqlite3.Row
        def cosine(first,second):
            a,b=json.loads(first),json.loads(second)
            return 1-sum(x*y for x,y in zip(a,b))/math.sqrt(sum(x*x for x in a)*sum(x*x for x in b))
        c.create_function("cosine_distance",2,cosine)
        try:
            yield SQLiteCloudConnection(c); c.commit()
        except BaseException:
            c.rollback(); raise
        finally: c.close()

    def put_pdf(self,access,doc,data=b"pdf"):
        with store.db() as c:
            cloud_files.put(c,access.workspace,doc,f"data/documents/{doc}/original.pdf",data)

    def test_cloud_mode_uses_disposable_cache_and_keeps_local_database_intact(self):
        before=self.database.read_bytes()
        self.assertEqual(store.root(),(self.temp/"disposable").resolve())
        store.initialize()
        self.assertEqual(self.database.read_bytes(),before)

    def test_document_reads_and_lists_enforce_workspace_boundaries(self):
        self.put_pdf(self.a,D1)
        self.assertEqual(store.document_bytes(self.a,D1),b"pdf")
        before=self.bucket.downloads
        self.assertRaises(PermissionError,store.document_bytes,self.b,D1)
        self.assertEqual(self.bucket.downloads,before)
        self.assertEqual([row["id"] for row in store.documents(self.b)],[D2])

    def test_bad_hash_never_returns_document_bytes(self):
        self.put_pdf(self.a,D1)
        key=next(iter(self.bucket.objects)); self.bucket.objects[key]=b"damaged"
        self.assertRaisesRegex(cloud_db.CloudStorageError,"checksum",store.document_bytes,self.a,D1)

    def test_pdf_cache_avoids_repeat_downloads_and_does_not_bypass_authorization(self):
        self.put_pdf(self.a,D1)
        store.document_bytes(self.a,D1); store.document_bytes(self.a,D1)
        self.assertEqual(self.bucket.downloads,1)
        with self.db() as c: c.execute("DELETE FROM members WHERE principal='alice'")
        self.assertRaises(PermissionError,store.document_bytes,self.a,D1)

    def test_revoked_membership_after_download_does_not_return_bytes(self):
        self.put_pdf(self.a,D1)
        original=self.bucket.download
        def revoke(key):
            with self.db() as c: c.execute("DELETE FROM members WHERE principal='alice'")
            return original(key)
        # SQLite's read lock prevents the concurrent writer; simulate the final auth check.
        with patch.object(store,"authorize",side_effect=[None,PermissionError("revoked")]):
            self.assertRaises(PermissionError,store.document_bytes,self.a,D1)

    def test_path_traversal_foreign_documents_and_manifest_tampering_are_rejected(self):
        for path in ("../secret",f"data/documents/{D1}/../../secret",f"data\\documents\\{D1}\\original.pdf",f"/data/documents/{D1}/x"):
            with self.subTest(path=path): self.assertRaises(cloud_db.CloudStorageError,cloud_files.relative_path,path)
        self.assertRaises(cloud_db.CloudStorageError,cloud_files.relative_path,f"data/documents/{D2}/original.pdf",D1)
        row={"workspace":W1,"document_id":D1,"path":f"data/documents/{D1}/original.pdf","sha":"0"*64,"size":3,"object_key":W2+"/foreign"}
        self.assertRaises(cloud_db.CloudStorageError,cloud_files.download,row)
        self.assertEqual(self.bucket.downloads,0)

    def test_hydrate_only_selected_documents_and_reuses_matching_local_files(self):
        self.put_pdf(self.a,D1); self.put_pdf(self.b,D2)
        cloud_files.hydrate(self.a,[D1]); cloud_files.hydrate(self.a,[D1])
        self.assertEqual(self.bucket.downloads,1)
        self.assertEqual(store.file_path(W1,"data/documents",D1,"original.pdf").read_bytes(),b"pdf")
        self.assertFalse((store.workspace_dir(W1)/"data/documents"/D2).exists())

    def test_publish_requires_current_job_token_and_membership(self):
        path=store.file_path(W1,"data/processed",D1,"document.json")
        path.parent.mkdir(parents=True); path.write_text('{}')
        cloud_files.publish(self.a,D1,J1,TOKEN)
        self.assertEqual(self.bucket.uploads,1)
        self.assertRaises(cloud_db.CloudStorageError,cloud_files.publish,self.a,D1,J1,"0"*32)
        with self.db() as c: c.execute("DELETE FROM members WHERE principal='alice'")
        self.assertRaises(PermissionError,cloud_files.publish,self.a,D1,J1,TOKEN)

    def test_unconfigured_connection_cannot_upload_orphan_objects(self):
        fake=types.ModuleType("pymupdf")
        fake.open=MagicMock()
        with patch.dict(sys.modules,{"pymupdf":fake}),patch.object(store,"check_pages"):
            # The real queue() refuses the missing OpenRouter connection before upload.
            with self.assertRaises(ValueError):
                store.upload(self.a,"new.pdf",b"different-pdf",{})
        self.assertEqual(self.bucket.uploads,0)
        with self.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM documents").fetchone()[0],2)

    def test_cloud_upload_survives_loss_of_all_local_cache_files(self):
        fake=types.ModuleType("pymupdf"); fake.open=MagicMock()
        from pilot import model_connections
        with patch.dict(sys.modules,{"pymupdf":fake}),patch.object(store,"check_pages"),patch.object(model_connections,"snapshot",return_value={"connection_version":"fixture","models":{}}):
            doc=store.upload(self.a,"new.pdf",b"different-pdf",{})
        self.assertEqual(store.document_bytes(self.a,doc),b"different-pdf")
        self.assertFalse((store.workspace_dir(W1)/"data/documents"/doc).exists())
        self.assertEqual(self.bucket.uploads,1)

    def test_delete_removes_all_versions_but_keeps_other_workspaces(self):
        self.put_pdf(self.a,D1); self.put_pdf(self.a,D1,b"new-pdf"); self.put_pdf(self.b,D2)
        self.assertEqual(len(self.bucket.objects),3)
        cloud_files.delete_document(self.a,D1)
        self.assertEqual(len(self.bucket.objects),1)
        self.assertTrue(next(iter(self.bucket.objects)).startswith(W2+"/"))

    def test_vector_search_is_scoped_to_both_document_and_workspace(self):
        a=Collection("collection","gemini-embedding-001",768)
        a.upsert(ids=["shared"],embeddings=[[1.0]+[0.0]*767],documents=["A text"],metadatas=[{"document_id":D1,"content_hash":"one"}])
        with self.db() as c:
            c.execute("INSERT INTO vector_collections VALUES(?,?,?,?)",(W2,"collection","gemini-embedding-001",768))
            c.execute("INSERT INTO vectors VALUES(?,?,?,?,?,?,?)",(W2,"collection","shared",D2,vector_literal([1.0]+[0.0]*767),"B private text",json.dumps({"document_id":D2})))
        result=a.query(query_embeddings=[[1.0]+[0.0]*767],where={"document_id":D1},n_results=5,include=["documents","metadatas"])
        self.assertEqual(result["documents"],[["A text"]])
        self.assertEqual(a.get(where={"document_id":D2},include=[])["ids"],[])
        a.delete(ids=["shared"])
        with self.db() as c: self.assertEqual(c.execute("SELECT COUNT(*) FROM vectors WHERE workspace=?",(W2,)).fetchone()[0],1)

    def test_vectors_cannot_be_written_after_job_ownership_changes(self):
        a=Collection("collection","gemini-embedding-001",768)
        with self.db() as c: c.execute("UPDATE jobs SET status='paused',run_token=NULL WHERE id=?",(J1,))
        self.assertRaises(cloud_db.CloudStorageError,a.upsert,ids=["id"],embeddings=[[1.0]+[0.0]*767],documents=["text"],metadatas=[{"document_id":D1}])
        with self.db() as c: self.assertEqual(c.execute("SELECT COUNT(*) FROM vectors").fetchone()[0],0)

    def test_invalid_dimensions_values_and_changed_models_never_rebuild_index(self):
        Collection("collection","original",768)
        self.assertRaises(cloud_db.CloudStorageError,Collection,"different-name","changed",768)
        for vector in ([1.0]*767,[0.0]*768,[float("nan")]+[0.0]*767,[True]+[0.0]*767):
            with self.subTest(vector_type=type(vector[0]).__name__): self.assertRaises(cloud_db.CloudStorageError,vector_literal,vector)

    def test_inventory_is_read_only_and_has_no_network_calls(self):
        before=self.database.read_bytes()
        with patch.object(cloud_db,"connect",side_effect=AssertionError("No network")):
            result=migration.inventory(self.temp)
        self.assertEqual(result["network_calls"],0); self.assertEqual(self.database.read_bytes(),before)
        self.assertRaises(cloud_db.CloudStorageError,migration.migrate,self.temp)

    def test_local_backup_cannot_be_reported_as_cloud_data_protection(self):
        from scripts.backup_restore import create_backup
        target=self.temp/"must-not-be-created"
        self.assertRaisesRegex(ValueError,"Postgres",create_backup,PROJECT,target,services_stopped=True)
        self.assertFalse(target.exists())

    def test_backup_cli_also_detects_cloud_mode_in_project_env_file(self):
        from scripts.backup_restore import create_backup
        project=self.temp/"cloud-project"; project.mkdir()
        (project/".env").write_text('CUSTOMER_STORAGE="supabase"\n')
        with patch.dict(os.environ,{},clear=True):
            self.assertRaisesRegex(ValueError,"Postgres",create_backup,project,self.temp/"no-backup",services_stopped=True)

    def test_windows_artifact_paths_are_portable_without_changing_evidence_values(self):
        original={"image_path":f"data\\table_images\\{D1}\\p1.png","rows":[["Revenue","100"]],"content_hash":"keep"}
        fixed=migration.portable_paths(original)
        self.assertEqual(fixed["image_path"],f"data/table_images/{D1}/p1.png")
        self.assertEqual(fixed["rows"],original["rows"]); self.assertEqual(fixed["content_hash"],"keep")

    def migration_fixture(self):
        from cryptography.fernet import Fernet
        self.stack.enter_context(patch.dict(os.environ,{"CUSTOMER_KEY_ENCRYPTION_KEY":Fernet.generate_key().decode()}))
        with self.db() as c: c.execute("UPDATE jobs SET status='complete',run_token=NULL")
        for ws,doc in ((W1,D1),(W2,D2)):
            base=self.temp/"workspaces"/ws/"data"
            pdf=base/"documents"/doc/"original.pdf"; pdf.parent.mkdir(parents=True); pdf.write_bytes(b"pdf")
            chunks=base/"processed"/doc/"chunks.json"; chunks.parent.mkdir(parents=True)
            chunks.write_text(json.dumps({"chunks":[{"id":"shared","text":"text"}]}))
        target=self.temp/"target.sqlite3"
        with closing(sqlite3.connect(self.database)) as source,closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
            for table in migration.TABLES: dest.execute("DELETE FROM "+table)
            dest.commit()
        self.database=target
        def batches(index,name,ids):
            for doc in ids:
                yield doc,{"ids":["shared"],"embeddings":[[1.0]+[0.0]*767],"documents":["text"],
                           "metadatas":[{"document_id":doc,"content_hash":hashlib.sha256(b"text").hexdigest()}]}
        self.stack.enter_context(patch.object(migration,"exported_vectors",side_effect=batches))

    def test_cold_migration_copies_records_and_vectors_without_modifying_source(self):
        self.migration_fixture()
        before=(self.temp/"control.sqlite3").read_bytes()
        result=migration.migrate(self.temp,services_stopped=True)
        self.assertEqual(result["vectors"],2); self.assertEqual(result["embedding_provider_calls"],0)
        self.assertEqual((self.temp/"control.sqlite3").read_bytes(),before)
        self.assertEqual(store.document_bytes(self.a,D1),b"pdf")
        self.assertRaises(PermissionError,store.document_bytes,self.b,D1)
        self.assertEqual(store.get_job(self.a,J1)["status"],"complete")
        self.assertRaises(cloud_db.CloudStorageError,migration.migrate,self.temp,services_stopped=True)

    def test_incomplete_source_vectors_roll_back_all_target_database_rows(self):
        self.migration_fixture()
        with patch.object(migration,"exported_vectors",return_value=iter([])):
            self.assertRaises(cloud_db.CloudStorageError,migration.migrate,self.temp,services_stopped=True)
        with self.db() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM documents").fetchone()[0],0)

    def encrypted_source_connection(self):
        from cryptography.fernet import Fernet
        secret=Fernet(os.environ["CUSTOMER_KEY_ENCRYPTION_KEY"].encode()).encrypt(json.dumps({"principal":"alice","version":"7"*32,"key":"fixture-not-a-provider-key"}).encode()).decode()
        with closing(sqlite3.connect(self.temp/"control.sqlite3")) as c:
            c.execute("INSERT INTO model_connections VALUES(?,?,?,?,?)",("alice","7"*32,secret,"{}",time.time()))
            c.commit()
        return secret

    def test_migration_preserves_encrypted_connections_without_provider_validation(self):
        self.migration_fixture(); encrypted=self.encrypted_source_connection()
        from pilot import model_connections
        with patch.object(model_connections,"validate",side_effect=AssertionError("No provider calls")):
            migration.migrate(self.temp,services_stopped=True)
        with self.db() as c: row=c.execute("SELECT * FROM model_connections WHERE principal='alice'").fetchone()
        self.assertEqual(row["secret"],encrypted)
        self.assertEqual(model_connections._decode(row),"fixture-not-a-provider-key")

    def test_wrong_master_key_refuses_migration_before_upload(self):
        self.migration_fixture(); self.encrypted_source_connection()
        from cryptography.fernet import Fernet
        with patch.dict(os.environ,{"CUSTOMER_KEY_ENCRYPTION_KEY":Fernet.generate_key().decode()}):
            self.assertRaises(cloud_db.CloudStorageError,migration.migrate,self.temp,services_stopped=True)
        self.assertEqual(self.bucket.uploads,0)

    def test_cloud_object_corruption_aborts_migration_and_rolls_back_records(self):
        self.migration_fixture()
        original=self.bucket.upload
        def corrupt(key,data,options): original(key,b"bad bytes",options)
        with patch.object(self.bucket,"upload",side_effect=corrupt):
            self.assertRaisesRegex(cloud_db.CloudStorageError,"checksum",migration.migrate,self.temp,services_stopped=True)
        with self.db() as c: self.assertEqual(c.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0],0)


if __name__=="__main__": unittest.main()
