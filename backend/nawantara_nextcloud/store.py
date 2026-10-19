"""Durable user-scoped connections and resumable save jobs."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet

CONNECTION_COLUMNS = ('user_id', 'username', 'secret', 'generation', 'instance')
OBSOLETE_TABLES = ('documents', 'exclusions', 'index_notifications', 'job_events')

SCHEMA = """
    CREATE TABLE IF NOT EXISTS connections (
        user_id TEXT PRIMARY KEY, username TEXT NOT NULL, secret BLOB NOT NULL,
        generation TEXT NOT NULL, instance TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, action TEXT NOT NULL,
        status TEXT NOT NULL, stage TEXT NOT NULL, payload BLOB NOT NULL,
        result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
        cancel INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
    CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC,id DESC);
    CREATE TABLE IF NOT EXISTS action_proposals (
        id TEXT PRIMARY KEY, user_id TEXT NOT NULL, operation TEXT NOT NULL,
        payload BLOB NOT NULL, status TEXT NOT NULL, result BLOB NOT NULL,
        created REAL NOT NULL, updated REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS processing_state (name TEXT PRIMARY KEY, value REAL NOT NULL);
"""


class Store:
    def __init__(self, directory: Path, key: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'nextcloud.sqlite3'
        self.cipher = Fernet(key.encode())
        with self.db() as db:
            db.executescript('PRAGMA journal_mode=WAL;')
            self.prepare(db)
        self.path.chmod(0o600)

    def prepare(self, db: sqlite3.Connection) -> None:
        """Drop tables for features that no longer exist instead of migrating them."""
        for table in OBSOLETE_TABLES:
            db.execute(f'DROP TABLE IF EXISTS {table}')
        existing = [row[1] for row in db.execute('PRAGMA table_info(connections)')]
        if existing and existing != list(CONNECTION_COLUMNS):
            # The stored scope selection belonged to indexing, so the connection is rebuilt.
            db.execute('DROP TABLE connections')
        db.executescript(SCHEMA)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def connect(self, user_id: str, username: str, password: str, instance: str) -> None:
        with self.db() as db:
            db.execute('DELETE FROM action_proposals WHERE user_id=?', (user_id,))
            db.execute("UPDATE jobs SET cancel=1 WHERE user_id=? AND status IN ('queued','processing')", (user_id,))
            db.execute(
                'INSERT OR REPLACE INTO connections VALUES (?,?,?,?,?)',
                (user_id, username, self.cipher.encrypt(password.encode()), uuid.uuid4().hex, instance),
            )

    def connection(self, user_id: str) -> dict:
        with self.db() as db:
            row = db.execute('SELECT * FROM connections WHERE user_id=?', (user_id,)).fetchone()
        if row is None:
            raise ValueError('Connect your Nextcloud account first')
        return {**dict(row), 'password': self.cipher.decrypt(row['secret']).decode()}

    def disconnect(self, user_id: str) -> None:
        with self.db() as db:
            db.execute('DELETE FROM connections WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM action_proposals WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM jobs WHERE user_id=?', (user_id,))

    def enqueue(self, user_id: str, action: str, payload: dict) -> str:
        account = self.connection(user_id)
        job_id = uuid.uuid4().hex
        now = time.time()
        with self.db() as db:
            db.execute(
                'INSERT INTO jobs(id,user_id,action,status,stage,payload,created,updated) '
                "VALUES (?,?,?,'queued','queued',?,?,?)",
                (
                    job_id,
                    user_id,
                    action,
                    self.cipher.encrypt(json.dumps({**payload, 'generation': account['generation']}).encode()),
                    now,
                    now,
                ),
            )
        return job_id

    def claim(self) -> dict | None:
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute(
                "UPDATE jobs SET status='processing',stage='starting',updated=? WHERE id=?", (time.time(), row['id'])
            )
        return {
            **dict(row),
            'payload': json.loads(self.cipher.decrypt(row['payload'])),
            'result': json.loads(row['result']),
        }

    def update(self, job_id: str, status: str, stage: str, result: dict | None = None, error: str = '') -> None:
        with self.db() as db:
            changed = db.execute(
                'UPDATE jobs SET status=?,stage=?,error=?,updated=? WHERE id=?',
                (status, stage, error, time.time(), job_id),
            ).rowcount
            if changed and result is not None:
                db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(result), job_id))

    def jobs(self, user_id: str) -> list[dict]:
        with self.db() as db:
            rows = db.execute(
                'SELECT id,user_id,action,status,stage,result,error,cancel,created,updated FROM jobs '
                'WHERE user_id=? ORDER BY created DESC LIMIT 100',
                (user_id,),
            ).fetchall()
        return [{**dict(row), 'result': json.loads(row['result'])} for row in rows]

    def control(self, job_id: str, user_id: str, operation: str) -> None:
        with self.db() as db:
            row = db.execute('SELECT status FROM jobs WHERE id=? AND user_id=?', (job_id, user_id)).fetchone()
            if row is None:
                raise ValueError('Job not found')
            if operation == 'cancel' and row['status'] in {'queued', 'processing'}:
                status = 'cancelled' if row['status'] == 'queued' else 'processing'
                db.execute(
                    'UPDATE jobs SET cancel=1,status=?,stage=?,updated=? WHERE id=?',
                    (status, 'cancellation_requested', time.time(), job_id),
                )
            elif operation == 'retry' and row['status'] in {'failed', 'interrupted', 'cancelled'}:
                db.execute(
                    "UPDATE jobs SET status='queued',stage='retry_queued',cancel=0,error='',updated=? WHERE id=?",
                    (time.time(), job_id),
                )
            else:
                raise ValueError('This action is not available for the current job status')

    def cancelled(self, job_id: str) -> bool:
        with self.db() as db:
            row = db.execute('SELECT cancel FROM jobs WHERE id=?', (job_id,)).fetchone()
        return row is None or bool(row['cancel'])

    def heartbeat(self) -> None:
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO processing_state VALUES ('heartbeat',?)", (time.time(),))

    def interrupt(self) -> None:
        """A process that stopped mid-write leaves an uncertain outcome, never a retry."""
        with self.db() as db:
            db.execute("UPDATE action_proposals SET status='uncertain' WHERE status='executing'")
            db.execute(
                "UPDATE jobs SET status='interrupted',stage='interrupted',updated=?,"
                "error='Processing interrupted. Retry to reconcile the saved checkpoint.' WHERE status='processing'",
                (time.time(),),
            )
