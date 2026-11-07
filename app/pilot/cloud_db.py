"""Server-only Postgres access, preserving the small store's SQL/row interface."""
from __future__ import annotations

from contextlib import contextmanager
import os
from urllib.parse import urlparse, parse_qs


class CloudStorageError(RuntimeError):
    """Safe operational message; never contains credentials or provider bodies."""


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
        raise CloudStorageError("Set CUSTOMER_DATABASE_URL to the Supabase session-pooler URI (port 5432) with TLS.") from None
    connection = None
    try:
        import psycopg
        connection = psycopg.connect(url, sslmode=parse_qs(parsed.query).get("sslmode", ["require"])[0],
                                     autocommit=True, prepare_threshold=None,connect_timeout=10)
        # Explicit SET also works through session poolers that ignore startup options.
        connection.execute("SET search_path=verifier,extensions,pg_catalog")
        connection.execute("SET statement_timeout=30000")
        connection.autocommit=autocommit
        return connection
    except Exception:
        if connection is not None:
            connection.close()
        raise CloudStorageError("The customer database could not be reached. Check its connection settings and service status.") from None


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
            raise CloudStorageError("Customer data could not be saved or loaded. Retry when the database is available.") from None
        raise
    finally:
        connection.close()


def initialize():
    # Schema installation is an explicit operator action, never a web request.
    with db() as c:
        row = c.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
        if not row or row[0] != 1:
            raise CloudStorageError("Apply the supplied Supabase storage schema before starting cloud mode.")


class WorkerLock:
    def __init__(self):
        self.connection = connect(autocommit=True)
        try:
            row=self.connection.execute("SELECT pg_try_advisory_lock(731905022),pg_backend_pid()").fetchone()
            if not row[0]:
                raise CloudStorageError("Another customer worker is already running.")
            self.pid=row[1]
        except BaseException as exc:
            self.connection.close()
            if isinstance(exc,CloudStorageError) or not isinstance(exc,Exception):
                raise
            raise CloudStorageError("The worker lock could not be acquired. Check the database session connection.") from None

    def check(self):
        try:
            if self.connection.execute("SELECT pg_backend_pid()").fetchone()[0] != self.pid:
                raise RuntimeError("Database session changed.")
        except Exception:
            raise CloudStorageError("The worker lost its database lock. Restart the worker and explicitly retry interrupted work.") from None

    def close(self):
        self.connection.close()
