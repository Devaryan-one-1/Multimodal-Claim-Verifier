"""Cloud contracts with temporary state; no Supabase, Google or OpenRouter calls."""
from contextlib import contextmanager, ExitStack, closing, redirect_stdout, redirect_stderr
import hashlib
import io
import json
import math
import os
import re
from pathlib import Path
import sqlite3
import sys
import tempfile
import textwrap
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
from scripts import test_supabase_storage as smoke_cli
from scripts import beta_status
from deploy import healthcheck
from test_customer_beta import load_module

W1,W2,D1,D2,J1,TOKEN=(char*32 for char in "abcdef")


class SchemaContractTests(unittest.TestCase):
    def setUp(self):
        self.sql=re.sub(r"--[^\n]*","",(PROJECT/"supabase/storage_schema.sql").read_text())

    def test_schema_setup_does_not_depend_on_editor_search_path(self):
        created=re.findall(r"CREATE TABLE IF NOT EXISTS ([\w.]+)",self.sql)
        self.assertEqual(len(created),12)
        self.assertTrue(all(name.startswith("verifier.") for name in created))
        references=re.findall(r"REFERENCES ([\w.]+)\(",self.sql)
        self.assertTrue(references)
        self.assertTrue(set(references).issubset(created))
        self.assertIn("INSERT INTO verifier.schema_version",self.sql)
        self.assertNotRegex(self.sql,r"SET\s+(?:LOCAL\s+)?search_path")

    def test_every_created_table_explicitly_enables_rls_and_revokes_browser_grants(self):
        created=set(re.findall(r"CREATE TABLE IF NOT EXISTS ([\w.]+)",self.sql))
        protected=set(re.findall(r"ALTER TABLE ([\w.]+) ENABLE ROW LEVEL SECURITY",self.sql))
        self.assertEqual(protected,created)
        revoked=re.search(r"REVOKE ALL ON TABLE (.*?) FROM PUBLIC, anon, authenticated;",self.sql,re.S)
        self.assertIsNotNone(revoked)
        self.assertEqual(set(re.findall(r"verifier\.\w+",revoked[1])),created)
        self.assertNotRegex(self.sql,r"DISABLE ROW LEVEL SECURITY|DROP TABLE|TRUNCATE")


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

    def test_connection_preserves_timestamp_roundtrip_with_rounded_server_default(self):
        timestamp=1794151029.1234567
        class RoundedSession:
            autocommit=True
            precise=False
            def execute(self,sql):
                if sql.startswith("SET extra_float_digits="):
                    self.precise=int(sql.partition("=")[2])>0
                value=timestamp if self.precise else float(format(timestamp,".15g"))
                return types.SimpleNamespace(fetchone=lambda:(value,))
        native=RoundedSession(); fake=types.SimpleNamespace(connect=MagicMock(return_value=native))
        self.assertNotEqual(native.execute("SELECT timestamp_value").fetchone()[0],timestamp)
        with patch.dict(os.environ,{"CUSTOMER_DATABASE_URL":"postgresql://user:PRIVATE@db:5432/postgres"}),patch.dict(sys.modules,{"psycopg":fake}):
            connected=cloud_db.connect()
        self.assertEqual(connected.execute("SELECT timestamp_value").fetchone()[0],timestamp)

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
            self.assertTrue(fake.SyncStorageClient.call_args.args[0].endswith("/storage/v1/"))
            headers=fake.SyncStorageClient.call_args.args[1]
            self.assertEqual(headers["apikey"],"sb_secret_fixture")
            self.assertNotIn("Authorization",headers)

    def test_cloud_failure_is_operational_and_provider_body_is_not_displayed(self):
        from services.failure_details import describe_failure
        details=describe_failure(cloud_db.CloudStorageError("PRIVATE_DATABASE_BODY"),calls=[{"status":"error","http_status":429}])
        self.assertEqual(details["code"],"cloud_storage")
        self.assertNotIn("PRIVATE",json.dumps(details))
        self.assertNotIn("http_status",details)


class CloudHeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {
            "CUSTOMER_STORAGE": "local", "CUSTOMER_DATA_ROOT": folder,
        }))
        store.initialize()
        self.native = self.stack.enter_context(closing(sqlite3.connect(Path(folder) / "control.sqlite3")))
        self.native.row_factory = sqlite3.Row
        self.database_time = 10000.0
        self.native.create_function("clock_timestamp", 0, lambda: self.database_time)
        self.native.execute("INSERT INTO workspaces VALUES(?,?)", (W1, "Fixture"))
        self.native.execute("INSERT INTO members VALUES(?,?)", ("owner", W1))
        self.native.commit()
        self.access = store.Access("owner", W1, 1e12)

        # Emulate only PostgreSQL's epoch expression; use real SQL/state for the
        # heartbeat, authorization, progress and monitor queries underneath it.
        self.connection = MagicMock()
        self.connection.execute.side_effect = lambda sql, parameters=(): self.native.execute(
            sql.replace("EXTRACT(EPOCH FROM clock_timestamp())::double precision", "clock_timestamp()"),
            parameters,
        )
        self.stack.enter_context(patch.dict(sys.modules, {
            "dotenv": types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None),
        }))
        self.worker = load_module(self.stack, "_cloud_heartbeat_worker", PROJECT / "app/pilot_worker.py")
        self.stack.enter_context(patch.dict(os.environ, {"CUSTOMER_STORAGE": "supabase"}))
        self.stack.enter_context(patch.object(self.worker, "db", self.database))
        self.stack.enter_context(patch.object(store, "db", self.database))
        self.stack.enter_context(patch.object(cloud_db, "db", self.database))

    @contextmanager
    def database(self):
        yield self.connection
        self.native.commit()

    def assert_health(self, expected):
        snapshot = store.progress_snapshot(self.access)
        self.assertEqual(snapshot["worker_available"], expected)
        self.assertEqual(healthcheck.healthy("worker"), expected)
        report = beta_status.snapshot(self.connection)
        self.assertEqual("worker_unavailable" not in report["warnings"], expected)
        return snapshot

    def test_different_worker_and_web_clocks_use_the_database_time(self):
        for worker_time, web_time in ((9000, 11000), (11000, 9000)):
            with self.subTest(worker_time=worker_time, web_time=web_time):
                with patch("time.time", return_value=worker_time):
                    self.worker.heartbeat()
                saved = self.native.execute("SELECT heartbeat FROM worker_status").fetchone()[0]
                self.assertEqual(saved, self.database_time)
                self.database_time += 1.25
                with patch("time.time", return_value=web_time):
                    self.assertEqual(self.assert_health(True)["worker_age_seconds"], 1.25)

    def test_actual_staleness_is_detected_despite_a_skewed_web_clock(self):
        self.worker.heartbeat()
        for age, available in ((14.99, True), (15, False), (60, False)):
            with self.subTest(age=age), patch("time.time", return_value=9000):
                self.database_time = 10000 + age
                self.assertAlmostEqual(self.assert_health(available)["worker_age_seconds"], age)
        self.worker.heartbeat()
        self.assert_health(True)

    def test_missing_and_future_legacy_heartbeats_are_unavailable(self):
        self.assertIsNone(self.assert_health(False)["worker_age_seconds"])
        self.native.execute("INSERT INTO worker_status VALUES(1,?)", (self.database_time + 30,))
        self.assertEqual(self.assert_health(False)["worker_age_seconds"], -30)
        self.worker.heartbeat()
        self.assert_health(True)

    def test_local_worker_keeps_its_existing_timestamp_format(self):
        with patch.dict(os.environ, {"CUSTOMER_STORAGE": "local"}), patch("time.time", return_value=12345.5):
            self.worker.heartbeat()
        saved = self.native.execute("SELECT heartbeat FROM worker_status").fetchone()[0]
        self.assertEqual(saved, 12345.5)


class SmokeDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(sys.modules,{"dotenv":types.SimpleNamespace(load_dotenv=MagicMock())}))
        self.stack.enter_context(patch.object(smoke_cli,"version",return_value="fixture"))
        self.stack.enter_context(patch.dict(os.environ,{"CUSTOMER_STORAGE":"local"}))

    def cli(self,*args):
        out,err=io.StringIO(),io.StringIO()
        with redirect_stdout(out),redirect_stderr(err):
            status=smoke_cli.main(list(args))
        return status,out.getvalue(),err.getvalue()

    def metadata(self,*,rls=True):
        c=MagicMock()
        c.execute.return_value.fetchone.side_effect=[
            {"table_count":12,"rls_enabled":rls},{"anon_access":False,"authenticated_access":False}]
        context=MagicMock(); context.__enter__.return_value=c
        self.stack.enter_context(patch.object(cloud_db,"initialize"))
        self.stack.enter_context(patch.object(cloud_db,"db",return_value=context))
        return c

    def test_database_diagnostics_keep_codes_without_credentials(self):
        cases=[("28P01","PRIVATE_URI","database_authentication"),("42P01","PRIVATE_URI","schema_missing"),
               ("42501","PRIVATE_URI","database_permission"),(None,"password authentication failed PRIVATE_URI","database_authentication"),
               (None,"Tenant or user not found PRIVATE_URI","database_tenant"),(None,"getaddrinfo failed PRIVATE_URI","database_dns"),
               (None,"connection timeout PRIVATE_URI","database_timeout")]
        for state,message,code in cases:
            with self.subTest(state=state,code=code):
                error=RuntimeError(message); error.sqlstate=state
                result=cloud_db.safe_diagnostic(cloud_db.database_error(error,connecting=True))
                self.assertEqual(result["code"],code)
                self.assertNotIn("PRIVATE_URI",json.dumps(result))
                self.assertEqual(result.get("sqlstate"),state)

    def test_driver_import_and_native_connection_errors_are_classified(self):
        self.assertEqual(cloud_db.database_error(ImportError("PRIVATE_PATH")).code,"driver_missing")
        fake=MagicMock(); fake.connect.side_effect=RuntimeError("password authentication failed PRIVATE_PASSWORD")
        with patch.dict(os.environ,{"CUSTOMER_DATABASE_URL":"postgresql://user:PRIVATE_PASSWORD@db:5432/postgres"}),patch.dict(sys.modules,{"psycopg":fake}):
            with self.assertRaises(cloud_db.CloudStorageError) as raised: cloud_db.connect()
        self.assertEqual(raised.exception.code,"database_authentication")
        self.assertNotIn("PRIVATE_PASSWORD",str(raised.exception))

    def test_storage_statuses_are_actionable_without_sdk_body(self):
        for status,code in (("401","storage_authentication"),(403,"storage_authentication"),(404,"bucket_missing"),(400,"storage_request"),(503,"storage_connection")):
            with self.subTest(status=status):
                error=RuntimeError("PRIVATE_SERVER_KEY"); error.status=status
                result=cloud_db.safe_diagnostic(cloud_files.storage_error(error))
                self.assertEqual(result["code"],code); self.assertEqual(result["http_status"],int(status))
                self.assertNotIn("PRIVATE_SERVER_KEY",json.dumps(result))
        self.assertEqual(cloud_files.storage_error(TypeError("PRIVATE_BODY")).code,"storage_dependency")
        public=cloud_db.CloudStorageError("PRIVATE_BODY",code="bucket_public")
        self.assertIs(cloud_files.storage_error(public),public)

    def test_untrusted_exception_fields_never_enter_diagnostics(self):
        errors=[RuntimeError("PRIVATE_PASSWORD"),AssertionError("PRIVATE_PASSWORD"),
                cloud_db.CloudStorageError("PRIVATE_PASSWORD"),
                cloud_db.CloudStorageError("PRIVATE_PASSWORD",code="PRIVATE_PASSWORD",sqlstate="PRIVATE_PASSWORD",http_status="PRIVATE_PASSWORD")]
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self.assertNotIn("PRIVATE_PASSWORD",json.dumps(cloud_db.safe_diagnostic(error)))

    def test_check_only_reads_metadata_in_local_mode_without_running_fixtures(self):
        c=self.metadata()
        with patch.object(cloud_files,"storage") as storage,patch.object(smoke_cli,"smoke") as fixtures:
            status,out,err=self.cli("--check-only")
        self.assertEqual(status,0); self.assertEqual(err,"")
        self.assertIn('"cloud_fixture_writes": false',out)
        self.assertIn('"customer_data_used": false',out)
        self.assertTrue(all(call.args[0].startswith("SELECT ") for call in c.execute.call_args_list))
        storage.assert_called_once(); fixtures.assert_not_called()
        self.assertEqual(os.environ["CUSTOMER_STORAGE"],"local")

    def test_database_failure_reports_exact_stage_without_connection_string(self):
        error=cloud_db.CloudStorageError("PRIVATE_URI",code="database_authentication",sqlstate="28P01")
        with patch.object(cloud_db,"initialize",side_effect=error),patch.object(cloud_files,"storage") as storage:
            status,out,err=self.cli("--check-only")
        result=json.loads(err)
        self.assertEqual(status,1); self.assertEqual(result["stage"],"database_schema")
        self.assertEqual(result["code"],"database_authentication"); self.assertEqual(result["sqlstate"],"28P01")
        self.assertEqual(result["provider_calls"],0); self.assertNotIn("PRIVATE_URI",out+err)
        storage.assert_not_called()

    def test_bucket_failure_reports_private_bucket_stage_and_status(self):
        self.metadata()
        error=cloud_db.CloudStorageError("PRIVATE_KEY",code="storage_authentication",http_status=401)
        with patch.object(cloud_files,"storage",side_effect=error): status,out,err=self.cli("--check-only")
        result=json.loads(err)
        self.assertEqual(status,1); self.assertEqual(result["stage"],"private_bucket")
        self.assertEqual(result["http_status"],401); self.assertNotIn("PRIVATE_KEY",out+err)

    def test_schema_protection_failure_stops_before_bucket_or_fixture_access(self):
        self.metadata(rls=False)
        with patch.object(cloud_files,"storage") as storage:
            status,_,err=self.cli("--check-only")
        self.assertEqual(status,1); self.assertEqual(json.loads(err)["code"],"schema_protection")
        storage.assert_not_called()

    def test_unexpected_failure_is_redacted_and_retains_stage(self):
        with patch.object(cloud_db,"initialize",side_effect=RuntimeError("PRIVATE_PASSWORD")):
            status,out,err=self.cli("--check-only")
        result=json.loads(err)
        self.assertEqual(status,1); self.assertEqual(result["code"],"unexpected_error")
        self.assertEqual(result["stage"],"database_schema")
        self.assertIn("package_versions",result); self.assertNotIn("PRIVATE_PASSWORD",out+err)

    def test_full_fixture_run_requires_explicit_cloud_mode(self):
        with patch.object(smoke_cli,"smoke") as fixtures:
            status,_,err=self.cli("--allow-cloud-write")
        self.assertEqual(status,1); self.assertEqual(json.loads(err)["code"],"storage_mode")
        fixtures.assert_not_called()

    def test_second_lock_connection_failure_is_not_a_passed_lock_check(self):
        stages=[]
        error=cloud_db.CloudStorageError("PRIVATE_BODY",code="database_connection")
        first=MagicMock()
        with patch.object(smoke_cli,"check_configuration"),patch.object(cloud_db,"WorkerLock",side_effect=[first,error]),patch.object(cloud_db,"db") as db:
            with self.assertRaises(smoke_cli.SmokeFailure) as raised: smoke_cli.smoke(stages.append)
        self.assertIs(raised.exception.error,error); self.assertEqual(raised.exception.passed,[])
        self.assertEqual(stages[-1],"worker_lock"); db.assert_not_called(); first.close.assert_called_once()


class MigrationRecordTests(unittest.TestCase):
    def rows(self,**tables):
        return {table:tables.get(table,[]) for table in migration.TABLES}

    def target(self,rows):
        result=MagicMock()
        result.execute.side_effect=lambda sql:types.SimpleNamespace(fetchall=lambda:rows[sql.split()[-1]])
        return result

    def test_comparison_uses_primary_keys_independent_of_returned_order(self):
        source=self.rows(workspaces=[{"id":"one","name":"A"},{"id":"two","name":"B"}],
                         decisions=[{"job":"job","atom":2,"note":"Two"},{"job":"job","atom":10,"note":"Ten"}])
        copied={table:list(reversed(rows)) for table,rows in source.items()}
        migration.verify_records(self.target(copied),source)

    def test_microsecond_timestamp_difference_still_rejects_records(self):
        stamp=1794151029.1234567
        source=self.rows(jobs=[{"id":"job","updated":stamp}])
        copied=self.rows(jobs=[{"id":"job","updated":float(format(stamp,".15g"))}])
        with self.assertRaises(migration.RecordMismatch) as raised:
            migration.verify_records(self.target(copied),source)
        self.assertEqual(raised.exception.details["table"],"jobs")
        self.assertEqual(raised.exception.details["fields"],["updated"])
        self.assertEqual(raised.exception.details["field_types"],{"updated":{"source":"float","target":"float"}})

    def test_ciphertext_and_serialized_json_must_remain_exact_without_value_disclosure(self):
        for field,old,new in (("secret","PRIVATE_SOURCE_CIPHERTEXT","PRIVATE_OTHER_CIPHERTEXT"),
                              ("models",'{"PRIVATE_MODEL":1}','{ "PRIVATE_MODEL": 1 }')):
            with self.subTest(field=field):
                source=self.rows(model_connections=[{"principal":"PRIVATE_ACCOUNT",field:old}])
                copied=self.rows(model_connections=[{"principal":"PRIVATE_ACCOUNT",field:new}])
                with self.assertRaises(migration.RecordMismatch) as raised:
                    migration.verify_records(self.target(copied),source)
                self.assertEqual(raised.exception.details["fields"],[field])
                self.assertNotIn("PRIVATE",str(raised.exception)+json.dumps(raised.exception.details))

    def test_changed_counts_keys_and_columns_are_rejected_with_safe_metadata(self):
        source=self.rows(workspaces=[{"id":"PRIVATE_ID","name":"PRIVATE_NAME"}])
        for records,reason in (([],"row_count"),([{"id":"PRIVATE_OTHER","name":"PRIVATE_NAME"}],"record_keys"),
                               ([{"id":"PRIVATE_ID","name":"PRIVATE_NAME","extra":None}],"column_names")):
            with self.subTest(reason=reason):
                with self.assertRaises(migration.RecordMismatch) as raised:
                    migration.verify_records(self.target(self.rows(workspaces=records)),source)
                self.assertEqual(raised.exception.details["reason"],reason)
                self.assertNotIn("PRIVATE",json.dumps(raised.exception.details))

    def test_duplicate_primary_keys_cannot_hide_a_missing_row(self):
        source=self.rows(workspaces=[{"id":"one","name":"A"},{"id":"two","name":"B"}])
        copied=self.rows(workspaces=[{"id":"one","name":"A"},{"id":"one","name":"A"}])
        with self.assertRaises(migration.RecordMismatch) as raised:
            migration.verify_records(self.target(copied),source)
        self.assertEqual(raised.exception.details["reason"],"record_keys")

    def run_cli(self,error):
        def fail(source,*,services_stopped,report):
            report("verify_records.jobs")
            raise error
        out,err=io.StringIO(),io.StringIO()
        with patch.dict(sys.modules,{"dotenv":types.SimpleNamespace(load_dotenv=MagicMock())}),patch.object(migration,"migrate",side_effect=fail),redirect_stdout(out),redirect_stderr(err):
            status=migration.main(["--apply","--services-stopped"])
        return status,out.getvalue(),err.getvalue()

    def test_cli_names_failed_fields_and_types_without_values(self):
        old=[{"id":"PRIVATE_ID","updated":1.000001}]; new=[{"id":"PRIVATE_ID","updated":1.0}]
        status,out,err=self.run_cli(migration.RecordMismatch("jobs",old,new,"field_values",["updated"],old[0],new[0]))
        result=json.loads(err)
        self.assertEqual(status,1); self.assertEqual(result["stage"],"verify_records.jobs")
        self.assertEqual(result["code"],"migration_records")
        self.assertEqual(result["details"]["fields"],["updated"])
        self.assertNotIn("PRIVATE_ID",out+err)

    def test_cli_redacts_unknown_cloud_and_runtime_error_bodies(self):
        for error in (cloud_db.CloudStorageError("PRIVATE_KEY"),PermissionError("PRIVATE_PATH"),
                      cloud_db.CloudStorageError("PRIVATE_URL",code="storage_connection",http_status=503)):
            with self.subTest(error=type(error).__name__):
                status,out,err=self.run_cli(error)
                result=json.loads(err)
                self.assertEqual(status,1); self.assertEqual(result["stage"],"verify_records.jobs")
                self.assertFalse(result["local_data_modified"])
                self.assertNotIn("PRIVATE",out+err)

    def test_cli_keeps_fixed_validation_messages_actionable(self):
        status,_,err=self.run_cli(migration.MigrationValidationError("The target customer schema is not empty. Migration will not overwrite it."))
        self.assertEqual(status,1); self.assertIn("not empty",json.loads(err)["message"])

    def test_default_inventory_is_unchanged_and_does_not_run_migration(self):
        out,err=io.StringIO(),io.StringIO()
        with patch.object(migration,"inventory",return_value={"mode":"inventory_only","network_calls":0}),patch.object(migration,"migrate") as apply,redirect_stdout(out),redirect_stderr(err):
            status=migration.main([])
        self.assertEqual(status,0); self.assertEqual(json.loads(out.getvalue())["network_calls"],0)
        self.assertEqual(err.getvalue(),""); apply.assert_not_called()


class CopiedIndexProcessTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack(); self.addCleanup(self.stack.close)
        self.temp=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.index=self.temp/"original"; self.index.mkdir()
        (self.index/"data_level0.bin").write_bytes(b"original-index-bytes")
        self.fixture={"collection":"fixture","ids":["one"],"embeddings":[[1.0]+[0.0]*767],
                      "documents":["PRIVATE_DOCUMENT"],"metadatas":[{"document_id":D1}]}
        self.configure()
        modules=self.temp/"modules"; package=modules/"chromadb"; package.mkdir(parents=True)
        (package/"config.py").write_text('class Settings:\n def __init__(self, **kwargs):\n  assert kwargs["anonymized_telemetry"] is False\n',encoding="utf-8")
        (package/"__init__.py").write_text(textwrap.dedent('''
            import json
            from pathlib import Path
            from types import SimpleNamespace
            _held=[]
            class Client:
                def __init__(self,path):
                    root=Path(path)
                    self.fixture=json.loads((root/'fixture.json').read_text())
                    # Simulate native handles surviving stop() until process exit.
                    _held.append((root/'data_level0.bin').open('rb'))
                    (root/'child-only.marker').write_text('opened only the copy')
                    self._system=SimpleNamespace(stop=lambda:None)
                def list_collections(self):
                    return [SimpleNamespace(name=self.fixture['collection'])]
                def get_collection(self,name,embedding_function):
                    assert embedding_function is None
                    return self
                def count(self): return len(self.fixture['ids'])
                def get(self,where,include,limit,offset):
                    if self.fixture.get('deny_vectors'):
                        error=PermissionError(13,'PRIVATE_NATIVE_ERROR','PRIVATE_FILENAME')
                        error.winerror=32
                        raise error
                    rows=[i for i,m in enumerate(self.fixture['metadatas']) if m['document_id']==where['document_id']][offset:offset+limit]
                    return {key:[self.fixture[key][i] for i in rows] for key in ('ids','embeddings','documents','metadatas')}
            def PersistentClient(path,settings): return Client(path)
        '''),encoding="utf-8")
        self.stack.enter_context(patch.dict(os.environ,{"PYTHONPATH":str(modules)}))
        self.factory=tempfile.TemporaryDirectory
        self.copies=[]
        def temporary(**kwargs):
            result=self.factory(dir=self.temp,**kwargs)
            self.copies.append(Path(result.name)); return result
        self.stack.enter_context(patch.object(migration.tempfile,"TemporaryDirectory",side_effect=temporary))

    def configure(self,**changes):
        self.fixture.update(changes)
        (self.index/"fixture.json").write_text(json.dumps(self.fixture),encoding="utf-8")

    def hashes(self):
        return {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.index.iterdir() if p.is_file()}

    def test_actual_child_releases_held_index_files_before_parent_cleanup(self):
        before=self.hashes()
        native=MagicMock(); native.PersistentClient.side_effect=AssertionError("Parent must not open Chroma")
        with patch.dict(sys.modules,{"chromadb":native}):
            result=list(migration.exported_vectors(self.index,"fixture",[D1]))
        self.assertEqual(result,[(D1,{k:self.fixture[k] for k in ("ids","embeddings","documents","metadatas")})])
        native.PersistentClient.assert_not_called()
        self.assertEqual(self.hashes(),before)
        self.assertTrue(self.copies); self.assertTrue(all(not path.exists() for path in self.copies))

    def test_child_permission_error_reports_operation_and_os_codes_without_private_paths(self):
        self.configure(deny_vectors=True)
        with self.assertRaises(migration.VectorExportError) as raised:
            list(migration.exported_vectors(self.index,"fixture",[D1]))
        details=raised.exception.details
        self.assertEqual(details["operation"],"read_vectors")
        self.assertEqual(details["errno"],13); self.assertEqual(details["winerror"],32)
        self.assertNotIn("PRIVATE",json.dumps(details)+str(raised.exception))
        self.assertTrue(all(not path.exists() for path in self.copies))

    def test_incompatible_collection_fails_without_rebuilding_original_index(self):
        before=self.hashes()
        with self.assertRaisesRegex(migration.MigrationValidationError,"incompatible"):
            list(migration.exported_vectors(self.index,"different",[D1]))
        self.assertEqual(self.hashes(),before)
        self.assertTrue(all(not path.exists() for path in self.copies))

    def test_native_child_crash_is_not_an_empty_success(self):
        with patch.object(migration.subprocess,"run",return_value=types.SimpleNamespace(returncode=7)):
            with self.assertRaises(migration.VectorExportError) as raised:
                list(migration.exported_vectors(self.index,"fixture",[D1]))
        self.assertEqual(raised.exception.details["operation"],"export_worker_result")
        self.assertEqual(raised.exception.details["exit_code"],7)
        self.assertTrue(all(not path.exists() for path in self.copies))

    def test_missing_source_index_never_starts_a_child(self):
        with patch.object(migration.subprocess,"run") as run:
            self.assertEqual(list(migration.exported_vectors(self.temp/"missing","fixture",[D1])),[])
        run.assert_not_called()

    def test_source_copy_access_failure_is_distinct_from_cleanup(self):
        with patch.object(migration.shutil,"copytree",side_effect=PermissionError(13,"PRIVATE_PATH")):
            with self.assertRaises(migration.VectorExportError) as raised:
                list(migration.exported_vectors(self.index,"fixture",[D1]))
        self.assertEqual(raised.exception.details["operation"],"copy_index")
        self.assertEqual(raised.exception.details["errno"],13)
        self.assertNotIn("PRIVATE_PATH",json.dumps(raised.exception.details))

    def test_worker_refuses_requests_outside_its_disposable_copy(self):
        request=self.index/"request.json"; request.write_text('{}')
        with patch.dict(sys.modules,{"chromadb":MagicMock()}) as modules:
            self.assertEqual(migration._export_worker(request),2)
            modules["chromadb"].PersistentClient.assert_not_called()
        self.assertFalse((self.index/"result.json").exists())


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
        sql=sql.replace("?::extensions.vector","?").replace("embedding::text","embedding").replace(" FOR UPDATE OF j","").replace(" FOR UPDATE","")
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

    def smoke_boundaries(self):
        self.stack.enter_context(patch.object(smoke_cli,"check_configuration"))
        self.stack.enter_context(patch.object(cloud_db,"WorkerLock",side_effect=[
            MagicMock(),cloud_db.CloudStorageError("Expected occupied lock",code="worker_active")]))

    def test_failed_fixture_manifest_write_cleans_objects_and_retains_primary_diagnostic(self):
        self.smoke_boundaries()
        before={k:os.environ.get(k) for k in ("CUSTOMER_WORKSPACE_ID","CUSTOMER_JOB_ID","CUSTOMER_JOB_TOKEN")}
        def interrupted_upload(c,ws,doc,path,data):
            key=cloud_files.object_key(ws,path,hashlib.sha256(data).hexdigest())
            self.bucket.upload(key,data,{})
            raise cloud_db.CloudStorageError("PRIVATE_DATABASE_BODY",code="database_query",sqlstate="42501")
        stages=[]
        with patch.object(cloud_files,"put",side_effect=interrupted_upload):
            with self.assertRaises(smoke_cli.SmokeFailure) as raised: smoke_cli.smoke(stages.append)
        self.assertEqual(raised.exception.error.code,"database_query")
        self.assertEqual(stages[-1],"private_object_upload")
        self.assertEqual(raised.exception.cleanup,[]); self.assertEqual(self.bucket.objects,{})
        with self.db() as c:
            self.assertEqual({r[0] for r in c.execute("SELECT id FROM workspaces")},{W1,W2})
        self.assertEqual({k:os.environ.get(k) for k in before},before)

    def test_failed_fixture_object_cleanup_retains_identities_for_operator_retry(self):
        self.smoke_boundaries()
        with patch.object(cloud_files,"put",side_effect=cloud_db.CloudStorageError("PRIVATE_BODY",code="storage_connection")),patch.object(cloud_files,"delete_document",side_effect=cloud_db.CloudStorageError("PRIVATE_KEY",code="storage_authentication",http_status=403)):
            with self.assertRaises(smoke_cli.SmokeFailure) as raised: smoke_cli.smoke()
        failure=raised.exception
        self.assertEqual(failure.error.code,"storage_connection")
        self.assertEqual(len(failure.cleanup),2); self.assertNotIn("PRIVATE",json.dumps(failure.cleanup))
        with self.db() as c:
            for ws in failure.workspaces:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM documents WHERE workspace=?",(ws,)).fetchone()[0],1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM members WHERE workspace=?",(ws,)).fetchone()[0],1)

    def test_stale_token_check_cannot_pass_on_unrelated_database_failure(self):
        self.smoke_boundaries()
        collections=[]
        for i in range(2):
            collection=MagicMock(); collection.query.return_value={"documents":[["Fixture "+str(i)]]}
            collection.get.return_value={"ids":[]}; collections.append(collection)
        error=cloud_db.CloudStorageError("PRIVATE_BODY",code="database_connection")
        collections[-1].delete.side_effect=error
        stages=[]
        with patch.object(smoke_cli,"Collection",side_effect=collections):
            with self.assertRaises(smoke_cli.SmokeFailure) as raised: smoke_cli.smoke(stages.append)
        self.assertIs(raised.exception.error,error)
        self.assertEqual(stages[-1],"stale_token_rejection")
        self.assertNotIn("Stale job token cannot modify vectors",raised.exception.passed)
        self.assertEqual(raised.exception.cleanup,[]); self.assertEqual(self.bucket.objects,{})

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

    def test_record_mismatch_rolls_back_before_any_file_or_vector_transfer(self):
        self.migration_fixture()
        before=(self.temp/"control.sqlite3").read_bytes()
        @contextmanager
        def mismatched_target():
            with self.db() as native:
                class Target:
                    def execute(self,sql,args=()):
                        result=native.execute(sql,args)
                        if sql=="SELECT * FROM jobs":
                            copied=[dict(row) for row in result.fetchall()]
                            copied[0]["updated"]+=0.000001
                            return types.SimpleNamespace(fetchall=lambda:copied)
                        return result
                yield Target()
        with patch.object(cloud_db,"db",mismatched_target),patch.object(migration,"exported_vectors") as vectors:
            with self.assertRaises(migration.RecordMismatch) as raised:
                migration.migrate(self.temp,services_stopped=True)
        self.assertEqual(raised.exception.details["table"],"jobs")
        self.assertEqual(self.bucket.uploads,0); vectors.assert_not_called()
        with self.db() as c: self.assertEqual(c.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0],0)
        self.assertEqual((self.temp/"control.sqlite3").read_bytes(),before)

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
