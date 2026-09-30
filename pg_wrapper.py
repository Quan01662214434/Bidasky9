import psycopg2
from psycopg2.extras import DictCursor
import re

class SQLiteToPostgresCursor:
    def __init__(self, pg_cursor):
        self.cursor = pg_cursor
        self.lastrowid = None
        self.rowcount = 0

    def execute(self, sql, params=()):
        # Replace ? with %s
        sql = sql.replace('?', '%s')
        
        # SQLite specific conversions at runtime just in case
        sql = re.sub(r"date\('now'\)", "CURRENT_DATE", sql, flags=re.IGNORECASE)
        sql = re.sub(r"\bIFNULL\b", "COALESCE", sql, flags=re.IGNORECASE)
        
        is_insert = sql.lstrip().upper().startswith("INSERT")
        
        if is_insert and "RETURNING " not in sql.upper():
            try:
                self.cursor.execute(sql + " RETURNING id", params)
                row = self.cursor.fetchone()
                self.lastrowid = row[0] if row else None
            except Exception:
                self.cursor.connection.rollback()
                self.cursor.execute(sql, params)
                self.lastrowid = None
        else:
            self.cursor.execute(sql, params)
            
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
        return SQLiteToPostgresCursor(self.conn.cursor(cursor_factory=DictCursor))
        
    def execute(self, sql, params=()):
        c = self.cursor()
        return c.execute(sql, params)
        
    def executescript(self, sql_script):
        c = self.cursor()
        # In postgres, we can execute multiple statements separated by semicolon
        # But we must convert ? to %s first (though scripts usually don't have params)
        c.execute(sql_script)
        
    def commit(self):
        self.conn.commit()
        
    def rollback(self):
        self.conn.rollback()
        
    def close(self):
        self.conn.close()

def connect(dsn, **kwargs):
    conn = psycopg2.connect(dsn)
    return SQLiteToPostgresConnection(conn)
