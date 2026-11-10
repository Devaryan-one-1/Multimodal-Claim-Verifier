"""Server-only Postgres access, preserving the small store's SQL/row interface."""
from __future__ import annotations

from contextlib import contextmanager
import os
import re
from urllib.parse import urlparse, parse_qs


class CloudStorageError(RuntimeError):
    """Safe operational message; never contains credentials or provider bodies."""

    def __init__(self,message,*,code="cloud_storage",sqlstate=None,http_status=None):
        super().__init__(message)
        self.code=code
        self.sqlstate=sqlstate if isinstance(sqlstate,str) and re.fullmatch(r"[A-Z0-9]{5}",sqlstate) else None
        self.http_status=http_status if isinstance(http_status,int) and not isinstance(http_status,bool) and 100<=http_status<=599 else None


DIAGNOSTICS = {
    "storage_mode": ("The full fixture smoke requires Supabase mode.","Keep local mode for --check-only, or set CUSTOMER_STORAGE=supabase temporarily before --allow-cloud-write."),
    "database_configuration": ("The database URI is invalid or uses transaction pooling.","Copy the Session pooler URI (port 5432), replace its password placeholder and URL-encode password symbols."),
    "driver_missing": ("The PostgreSQL driver could not load.","In the same Python environment, run: python -m pip install psycopg[binary]==3.3.8"),
    "database_authentication": ("Supabase rejected the database credentials.","Check the database password and the copied Session pooler username. URL-encode password symbols; this is the database password, separate from your Supabase sign-in password."),
    "database_tenant": ("The pooler could not identify the project or database user.","Copy the complete Session pooler URI from this project's Connect dialog, preserving its username and hostname."),
    "database_dns": ("The database hostname could not be resolved.","Copy the pooler hostname again and check your DNS/network connection."),
    "database_tls": ("The encrypted database connection could not be established.","Keep TLS enabled and check certificate settings, network filtering and the project connection instructions."),
    "database_timeout": ("The database connection or query timed out.","Check that the project is active and reachable; retry after checking network access and database load."),
    "database_capacity": ("The database reached a resource or connection limit.","Check Supabase database health, connection limits and usage, then retry."),
    "database_connection": ("The customer database connection failed.","Check the project's status, Session pooler URI, database password and network access to port 5432."),
    "schema_missing": ("A required customer table is missing from this database.","Run the complete updated supabase/storage_schema.sql in the same project and confirm schema version 1."),
    "schema_protection": ("The customer schema's RLS or browser-access restrictions do not match the setup.","Run the complete updated schema file and confirm all 12 verifier tables have RLS enabled."),
    "database_permission": ("The database user lacks permission for this operation.","Use this project's owner Session pooler connection and keep the verifier schema private."),
    "vector_extension": ("The required vector type or operator is unavailable.","Check that the vector extension is installed in the extensions schema as required by storage_schema.sql."),
    "database_query": ("A customer database query failed.","Confirm the updated schema and application versions match; report this stage and SQLSTATE without sharing credentials."),
    "storage_configuration": ("Private Storage configuration is incomplete.","Set this project's HTTPS SUPABASE_URL and server-only SUPABASE_SECRET_KEY."),
    "storage_dependency": ("The Python Storage SDK could not load or has an incompatible interface.","Use the project environment and install its pinned Supabase dependencies before rerunning."),
    "storage_authentication": ("Supabase Storage rejected the server key or its permissions.","Use this project's sb_secret_ server key, keeping the publishable key for customer login."),
    "storage_request": ("Supabase Storage rejected the request.","Check the matching project URL, server key and verifier-private bucket; report this stage and HTTP status if they are correct."),
    "bucket_missing": ("The verifier-private bucket was not found.","Apply the complete storage schema in the project matching SUPABASE_URL."),
    "bucket_public": ("The verifier-private bucket is not private.","Set the bucket to Private before rerunning; do not upload customer documents yet."),
    "storage_connection": ("The private Storage request failed.","Check Supabase Storage health, storage quota and connectivity, then retry."),
    "worker_active": ("Another cloud worker already holds the worker lock.","Stop the cloud worker before running the exclusive-lock smoke test; local-mode workers use a separate lock."),
    "worker_session": ("The worker database session changed or disconnected.","Use the Session pooler on port 5432 and rerun after checking database availability."),
    "job_ownership_changed": ("The request lost its job token or workspace membership.","An interrupted request must be explicitly retried; the smoke test must reject stale tokens."),
    "environment_dependency": ("Project dependencies are missing in this Python environment.","Activate the usual project environment and install its customer dependencies plus psycopg[binary]==3.3.8."),
    "fixture_check": ("A storage fixture assertion failed.","Report the failing stage and completed checks. Do not migrate customer data until it passes."),
    "fixture_cleanup": ("Disposable fixture cleanup did not complete.","Report the cleanup diagnostics and fixture workspace IDs before retrying or migrating customer data."),
    "migration_records": ("Migrated customer records differ; the database transaction was rolled back.","Report the table, field names and types from this diagnostic. Keep services stopped and preserve the source backup and encryption key."),
    "migration_vector_export": ("The copied index could not be exported or cleaned up safely.","Report the export operation and Windows/OS error code. Keep the apps and worker stopped; do not delete or rebuild the original index."),
    "cloud_storage": ("Cloud storage could not complete this operation safely.","Report the failing stage and any diagnostic code; keep the app in local mode until the smoke passes."),
    "unexpected_error": ("The smoke test encountered an unexpected error.","Report the failing stage and installed package versions. Raw exception text is withheld to protect credentials."),
}


def safe_diagnostic(error):
    code=error.code if isinstance(error,CloudStorageError) else "fixture_check" if isinstance(error,AssertionError) else "unexpected_error"
    if code not in DIAGNOSTICS: code="unexpected_error"
    message,action=DIAGNOSTICS[code]
    result={"code":code,"message":message,"action":action}
    if isinstance(error,CloudStorageError):
        if error.sqlstate: result["sqlstate"]=error.sqlstate
        if error.http_status: result["http_status"]=error.http_status
    return result


def database_error(error,*,connecting=False):
    """Classify privately; neither libpq's message nor the URI is returned."""
    state=getattr(error,"sqlstate",None)
    if not isinstance(state,str): state=None
    code={"28P01":"database_authentication","28000":"database_authentication","42P01":"schema_missing",
          "42501":"database_permission","42704":"vector_extension","42883":"vector_extension",
          "57014":"database_timeout","53100":"database_capacity","53200":"database_capacity",
          "53300":"database_capacity","53400":"database_capacity"}.get(state)
    text=str(error).casefold()
    if isinstance(error,ImportError): code="driver_missing"
    elif code is None:
        if "password authentication failed" in text: code="database_authentication"
        elif "tenant or user not found" in text: code="database_tenant"
        elif any(value in text for value in ("getaddrinfo", "could not translate host", "name or service not known")): code="database_dns"
        elif any(value in text for value in ("ssl", "certificate", "tls")): code="database_tls"
        elif any(value in text for value in ("timeout", "timed out")): code="database_timeout"
        else: code="database_connection" if connecting else "database_query"
    return CloudStorageError(DIAGNOSTICS[code][0],code=code,sqlstate=state)


def enabled():
    mode = os.getenv("CUSTOMER_STORAGE", "local").strip().lower()
    if mode not in {"local", "supabase"}:
        raise CloudStorageError("Set CUSTOMER_STORAGE to local or supabase.")
    return mode == "supabase"


def connect(*, autocommit=False):
    url = os.getenv("CUSTOMER_DATABASE_URL", "")
    try:
        parsed = urlparse(url)
        # A session connection is also used for the single-worker lock.
        if (parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname
                or parsed.port == 6543 or not parsed.username or not parsed.password
                or parse_qs(parsed.query).get("sslmode", ["require"])[0]
                not in {"require", "verify-ca", "verify-full"}):
            raise ValueError()
    except ValueError:
        raise CloudStorageError("Set CUSTOMER_DATABASE_URL to the Supabase session-pooler URI (port 5432) with TLS.",code="database_configuration") from None
    connection = None
    try:
        import psycopg
        connection = psycopg.connect(url, sslmode=parse_qs(parsed.query).get("sslmode", ["require"])[0],
                                     autocommit=True, prepare_threshold=None,connect_timeout=10)
        # Explicit SET also works through session poolers that ignore startup options.
        connection.execute("SET search_path=verifier,extensions,pg_catalog")
        connection.execute("SET statement_timeout=30000")
        # Round-trip stored timestamps exactly even if the pooler's session
        # defaults select PostgreSQL's older, rounded float text output.
        connection.execute("SET extra_float_digits=3")
        connection.autocommit=autocommit
        return connection
    except Exception as error:
        if connection is not None:
            connection.close()
        raise database_error(error,connecting=True) from None


class Row(dict):
    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def _row(self, value):
        return None if value is None else Row(zip((d.name for d in self.cursor.description), value))

    def fetchone(self):
        return self._row(self.cursor.fetchone())

    def fetchall(self):
        return [self._row(row) for row in self.cursor.fetchall()]

    def __iter__(self):
        for row in self.cursor:
            yield self._row(row)


def placeholders(sql):
    """Translate our bound qmark SQL; leave quoted literals/identifiers alone."""
    out, quote, index = [], None, 0
    while index < len(sql):
        char = sql[index]
        if char == "%":
            out.append("%%")
        elif quote:
            out.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    out.append(quote); index += 1
                else:
                    quote = None
        elif char in {"'", '"'}:
            quote = char; out.append(char)
        elif char == "?":
            out.append("%s")
        else:
            out.append(char)
        index += 1
    return "".join(out)


class Connection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, parameters=()):
        if sql.strip().upper() == "BEGIN IMMEDIATE":
            # Serialize the existing check-then-write operations across hosts.
            return Cursor(self.connection.execute("SELECT pg_advisory_xact_lock(731905021)"))
        if sql.startswith("INSERT OR IGNORE INTO "):
            sql = sql.replace("INSERT OR IGNORE INTO ", "INSERT INTO ", 1) + " ON CONFLICT DO NOTHING"
        return Cursor(self.connection.execute(placeholders(sql), parameters))


@contextmanager
def db():
    connection = connect()
    try:
        yield Connection(connection)
        connection.commit()
    except BaseException as exc:
        try:
            connection.rollback()
        except Exception:
            pass
        if exc.__class__.__module__.startswith("psycopg"):
            raise database_error(exc) from None
        raise
    finally:
        connection.close()


def initialize():
    # Schema installation is an explicit operator action, never a web request.
    with db() as c:
        row = c.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        if not row or row[0] != 1:
            raise CloudStorageError("Apply the supplied Supabase storage schema before starting cloud mode.",code="schema_missing")


class WorkerLock:
    def __init__(self):
        self.connection = connect(autocommit=True)
        try:
            row=self.connection.execute("SELECT pg_try_advisory_lock(731905022),pg_backend_pid()").fetchone()
            if not row[0]:
                raise CloudStorageError("Another customer worker is already running.",code="worker_active")
            self.pid=row[1]
        except BaseException as exc:
            self.connection.close()
            if isinstance(exc,CloudStorageError) or not isinstance(exc,Exception):
                raise
            raise database_error(exc,connecting=True) from None

    def check(self):
        try:
            if self.connection.execute("SELECT pg_backend_pid()").fetchone()[0] != self.pid:
                raise RuntimeError("Database session changed.")
        except Exception:
            raise CloudStorageError("The worker lost its database lock. Restart the worker and explicitly retry interrupted work.",code="worker_session") from None

    def close(self):
        self.connection.close()
