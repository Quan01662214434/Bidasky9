import psycopg2
from psycopg2.extras import DictCursor
import re
import logging

logger = logging.getLogger(__name__)


class SQLiteToPostgresCursor:
    def __init__(self, pg_conn):
        self.connection = pg_conn  # raw psycopg2 connection
        self.cursor = pg_conn.cursor(cursor_factory=DictCursor)
        self.lastrowid = None
        self.rowcount = 0

    def _convert_sql(self, sql):
        """Convert SQLite SQL to PostgreSQL compatible SQL."""
        # Replace ? with %s
        sql = sql.replace('?', '%s')

        # date('now') → CURRENT_DATE::text
        sql = re.sub(r"date\('now'\)", "CURRENT_DATE::text", sql, flags=re.IGNORECASE)

        # date(?, '-3 days') → not handled here, should be done in Python
        # But just in case, convert simple date() arithmetic
        sql = re.sub(r"date\(%s,\s*'(-?\d+) days?'\)", r"(%s::date + interval '\1 days')::text", sql, flags=re.IGNORECASE)

        # CURRENT_DATE (standalone, not already cast) → CURRENT_DATE::text
        # This avoids type mismatch between text columns and date type
        sql = re.sub(r"\bCURRENT_DATE\b(?!::)", "CURRENT_DATE::text", sql, flags=re.IGNORECASE)

        # Clean up double casts
        sql = sql.replace("CURRENT_DATE::text::text", "CURRENT_DATE::text")

        # IFNULL → COALESCE
        sql = re.sub(r"\bIFNULL\b", "COALESCE", sql, flags=re.IGNORECASE)

        return sql

    def execute(self, sql, params=()):
        if params is None:
            params = ()
        params = tuple(1 if p is True else 0 if p is False else p for p in params)

        sql = self._convert_sql(sql)

        is_insert = sql.lstrip().upper().startswith("INSERT")

        try:
            if is_insert and "RETURNING " not in sql.upper():
                try:
                    self.cursor.execute(sql + " RETURNING id", params)
                    row = self.cursor.fetchone()
                    self.lastrowid = row[0] if row else None
                except Exception:
                    self.connection.rollback()
                    self.cursor = self.connection.cursor(cursor_factory=DictCursor)
                    self.cursor.execute(sql, params)
                    self.lastrowid = None
            else:
                self.cursor.execute(sql, params)
        except Exception as e:
            # Rollback to clear the failed transaction state
            try:
                self.connection.rollback()
            except Exception:
                pass
            # Get a fresh cursor
            try:
                self.cursor = self.connection.cursor(cursor_factory=DictCursor)
            except Exception:
                pass
            logger.error(f"SQL Error: {e}\nSQL: {sql}\nParams: {params}")
            raise e

        self.rowcount = self.cursor.rowcount
        return self

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def fetchmany(self, size):
        return self.cursor.fetchmany(size)

    def close(self):
        self.cursor.close()


class SQLiteToPostgresConnection:
    def __init__(self, pg_conn):
        self.conn = pg_conn
        self.row_factory = None

    def cursor(self):
        return SQLiteToPostgresCursor(self.conn)

    def execute(self, sql, params=()):
        c = self.cursor()
        return c.execute(sql, params)

    def executescript(self, sql_script):
        c = self.cursor()
        c.execute(sql_script)

    def commit(self):
        # With autocommit=True, each statement is auto-committed.
        # This is a no-op for compatibility with existing code.
        pass

    def rollback(self):
        # With autocommit=True, rollback is generally not needed.
        # But just in case:
        try:
            self.conn.rollback()
        except Exception:
            pass

    def close(self):
        self.conn.close()



def connect(dsn, **kwargs):
    conn = psycopg2.connect(dsn)
    # Set autocommit so each query runs independently
    # This prevents one failed query from poisoning the entire connection
    conn.autocommit = True
    return SQLiteToPostgresConnection(conn)
