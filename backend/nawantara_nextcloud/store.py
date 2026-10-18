"""Durable user-scoped connections, derived index and resumable jobs."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from cryptography.fernet import Fernet

from .activity import record


class Store:
    def __init__(self, directory: Path, key: str) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / 'nextcloud.sqlite3'
        self.cipher = Fernet(key.encode())
        with self.db() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS connections (
                    user_id TEXT PRIMARY KEY, username TEXT NOT NULL, secret BLOB NOT NULL,
                    scopes TEXT NOT NULL, generation TEXT NOT NULL, instance TEXT NOT NULL,
                    synced REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS documents (
                    user_id TEXT NOT NULL, file_id TEXT NOT NULL, path TEXT NOT NULL,
                    etag TEXT NOT NULL, digest TEXT NOT NULL, chunks TEXT NOT NULL,
                    vectors TEXT NOT NULL, model TEXT NOT NULL,
                    PRIMARY KEY(user_id, file_id));
                CREATE TABLE IF NOT EXISTS exclusions (user_id TEXT, file_id TEXT, PRIMARY KEY(user_id,file_id));
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, action TEXT NOT NULL,
                    status TEXT NOT NULL, stage TEXT NOT NULL, payload BLOB NOT NULL,
                    result TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '',
                    cancel INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS job_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
                    occurred REAL NOT NULL, status TEXT NOT NULL, stage TEXT NOT NULL, details TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS job_events_job ON job_events(job_id,id);
                CREATE INDEX IF NOT EXISTS jobs_created ON jobs(created DESC,id DESC);
                CREATE TABLE IF NOT EXISTS processing_state (name TEXT PRIMARY KEY, value REAL NOT NULL);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def discard_other_embeddings(self, model: str) -> None:
        """Delete incompatible derived vectors and schedule their owners for rebuilding."""
        with self.db() as db:
            db.execute(
                'UPDATE connections SET synced=0 WHERE user_id IN (SELECT user_id FROM documents WHERE model != ?)',
                (model,),
            )
            db.execute('DELETE FROM documents WHERE model != ?', (model,))

    def connect(self, user_id: str, username: str, password: str, scopes: list[str], instance: str) -> None:
        with self.db() as db:
            db.execute('DELETE FROM documents WHERE user_id=?', (user_id,))
            db.execute("UPDATE jobs SET cancel=1 WHERE user_id=? AND status IN ('queued','processing')", (user_id,))
            db.execute(
                'INSERT OR REPLACE INTO connections VALUES (?,?,?,?,?,?,0)',
                (
                    user_id,
                    username,
                    self.cipher.encrypt(password.encode()),
                    json.dumps(scopes),
                    uuid.uuid4().hex,
                    instance,
                ),
            )

    def connection(self, user_id: str) -> dict:
        with self.db() as db:
            row = db.execute('SELECT * FROM connections WHERE user_id=?', (user_id,)).fetchone()
        if row is None:
            raise ValueError('Connect your Nextcloud account first')
        return {
            **dict(row),
            'password': self.cipher.decrypt(row['secret']).decode(),
            'scopes': json.loads(row['scopes']),
        }

    def disconnect(self, user_id: str) -> None:
        with self.db() as db:
            db.execute('DELETE FROM connections WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM exclusions WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM documents WHERE user_id=?', (user_id,))
            db.execute('DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE user_id=?)', (user_id,))
            db.execute('DELETE FROM jobs WHERE user_id=?', (user_id,))

    def enqueue(self, user_id: str, action: str, payload: dict, *, origin: str = 'queued') -> str:
        account = self.connection(user_id)
        payload = {**payload, 'generation': account['generation']}
        job_id = uuid.uuid4().hex
        now = time.time()
        with self.db() as db:
            if action == 'index':
                row = db.execute(
                    "SELECT id FROM jobs WHERE user_id=? AND action='index' "
                    "AND status IN ('queued','processing') AND cancel=0",
                    (user_id,),
                ).fetchone()
                if row:
                    return row['id']
            db.execute(
                'INSERT INTO jobs(id,user_id,action,status,stage,payload,created,updated) '
                "VALUES (?,?,?,'queued','queued',?,?,?)",
                (job_id, user_id, action, self.cipher.encrypt(json.dumps(payload).encode()), now, now),
            )
            if origin not in {'queued', 'scheduled', 'connection', 'followup'}:
                raise ValueError('Unknown job origin')
            record(db, job_id, 'queued', origin)
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
            record(db, row['id'], 'processing', 'starting')
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
            if not changed:
                return
            if result is not None:
                db.execute('UPDATE jobs SET result=? WHERE id=?', (json.dumps(result), job_id))
            record(db, job_id, status, stage, result)

    def jobs(self, user_id: str | None) -> list[dict]:
        with self.db() as db:
            rows = db.execute(
                'SELECT id,user_id,action,status,stage,result,error,cancel,created,updated FROM jobs '
                + ('WHERE user_id=? ' if user_id else '')
                + 'ORDER BY created DESC LIMIT 100',
                (user_id,) if user_id else (),
            ).fetchall()
        return [{**dict(row), 'result': json.loads(row['result']) if user_id else {}} for row in rows]

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
                record(db, job_id, status, 'cancellation_requested')
            elif operation == 'retry' and row['status'] in {'failed', 'interrupted', 'cancelled'}:
                db.execute(
                    "UPDATE jobs SET status='queued',stage='retry_queued',cancel=0,error='',updated=? WHERE id=?",
                    (time.time(), job_id),
                )
                record(db, job_id, 'queued', 'retry_queued')
            else:
                raise ValueError('This action is not available for the current job status')

    def heartbeat(self) -> None:
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO processing_state VALUES ('heartbeat',?)", (time.time(),))

    def cancelled(self, job_id: str) -> bool:
        with self.db() as db:
            row = db.execute('SELECT cancel FROM jobs WHERE id=?', (job_id,)).fetchone()
        return row is None or bool(row['cancel'])

    def documents(self, user_id: str) -> list[dict]:
        with self.db() as db:
            rows = db.execute('SELECT * FROM documents WHERE user_id=?', (user_id,)).fetchall()
        return [
            {**dict(row), 'chunks': json.loads(row['chunks']), 'vectors': json.loads(row['vectors'])} for row in rows
        ]

    def put_document(self, user_id: str, source: dict, generation: str) -> None:
        with self.db() as db:
            account = db.execute('SELECT generation FROM connections WHERE user_id=?', (user_id,)).fetchone()
            if account is None or account['generation'] != generation:
                raise ValueError('Account connection changed')
            if db.execute(
                'SELECT 1 FROM exclusions WHERE user_id=? AND file_id=?', (user_id, source['file_id'])
            ).fetchone():
                return
            db.execute(
                'INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?)',
                (
                    user_id,
                    source['file_id'],
                    source['path'],
                    source['etag'],
                    source['digest'],
                    json.dumps(source['chunks']),
                    json.dumps(source['vectors']),
                    source['model'],
                ),
            )

    def remove_document(self, user_id: str, file_id: str) -> None:
        with self.db() as db:
            db.execute('DELETE FROM documents WHERE user_id=? AND file_id=?', (user_id, file_id))

    def due_accounts(self) -> list[str]:
        with self.db() as db:
            return [
                row['user_id']
                for row in db.execute('SELECT user_id FROM connections WHERE synced < ?', (time.time() - 300,))
            ]

    def synced(self, user_id: str) -> None:
        with self.db() as db:
            db.execute('UPDATE connections SET synced=? WHERE user_id=?', (time.time(), user_id))

    def interrupt(self) -> None:
        with self.db() as db:
            rows = db.execute("SELECT id FROM jobs WHERE status='processing'").fetchall()
            db.execute(
                "UPDATE jobs SET status='interrupted',stage='interrupted',updated=?,"
                "error='Processing interrupted. Retry to reconcile the saved checkpoint.' WHERE status='processing'",
                (time.time(),),
            )
            for row in rows:
                record(db, row['id'], 'interrupted', 'interrupted')

    def exclude(self, user_id: str, file_id: str) -> None:
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO exclusions VALUES (?,?)', (user_id, file_id))
            db.execute('DELETE FROM documents WHERE user_id=? AND file_id=?', (user_id, file_id))

    def excluded(self, user_id: str) -> set[str]:
        with self.db() as db:
            return {r['file_id'] for r in db.execute('SELECT file_id FROM exclusions WHERE user_id=?', (user_id,))}
