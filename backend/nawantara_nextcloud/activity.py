"""Processing history with an explicit boundary around administrator-visible data."""

from __future__ import annotations

import json
import time

STATUSES = {'queued', 'processing', 'completed', 'failed', 'cancelled', 'interrupted'}
COLUMNS = 'id,user_id,action,status,stage,result,error,cancel,created,updated'


def counters(result: dict) -> dict:
    return {
        key: value
        for key, value in result.items()
        if key in {'indexed', 'total', 'processed', 'skipped'} and type(value) is int and value >= 0
    }


def record(db, job_id: str, status: str, stage: str, result: dict | None = None) -> None:
    db.execute(
        'INSERT INTO job_events(job_id,occurred,status,stage,details) VALUES (?,?,?,?,?)',
        (job_id, time.time(), status, stage, json.dumps(counters(result or {}))),
    )


def serialize(row, owner: bool = False) -> dict:
    item = dict(row)
    result = json.loads(item['result'])
    item['progress'] = counters(result)
    item['result'] = result if owner else {}
    return item


def listing(
    store,
    *,
    page: int = 1,
    status: str = '',
    action: str = '',
    actor: str = '',
    query: str = '',
    since: float = 0,
    owner: str | None = None,
) -> dict:
    clauses, params = ['created >= ?'], [since]
    if owner:
        clauses.append('user_id=?')
        params.append(owner)
    elif actor:
        clauses.append('user_id=?')
        params.append(actor)
    if action:
        clauses.append('action=?')
        params.append(action)
    if query:
        clauses.append('instr(id,?) > 0')
        params.append(query)
    where = ' AND '.join(clauses)
    with store.db() as db:
        summary = {key: 0 for key in STATUSES}
        for row in db.execute('SELECT status,count(*) AS n FROM jobs WHERE ' + where + ' GROUP BY status', params):
            summary[row['status']] = row['n']
        if status == 'attention':
            where += " AND status IN ('failed','interrupted')"
        elif status == 'active':
            where += " AND status IN ('queued','processing')"
        elif status:
            where += ' AND status=?'
            params.append(status)
        total = db.execute('SELECT count(*) FROM jobs WHERE ' + where, params).fetchone()[0]
        page = min(page, max(1, (total + 19) // 20))
        rows = db.execute(
            'SELECT ' + COLUMNS + ' FROM jobs WHERE ' + where + ' ORDER BY created DESC,id DESC LIMIT 20 OFFSET ?',
            [*params, (page - 1) * 20],
        ).fetchall()
        actors = [r[0] for r in db.execute('SELECT DISTINCT user_id FROM jobs ORDER BY user_id')]
        heartbeat = db.execute("SELECT value FROM processing_state WHERE name='heartbeat'").fetchone()
    return {
        'jobs': [serialize(row, owner is not None) for row in rows],
        'total': total,
        'page': page,
        'page_size': 20,
        'summary': summary,
        'actors': actors if owner is None else [],
        'heartbeat': heartbeat[0] if heartbeat else None,
        'observed_at': time.time(),
    }


def detail(store, job_id: str, owner: str | None = None) -> dict:
    with store.db() as db:
        row = db.execute(
            'SELECT ' + COLUMNS + ' FROM jobs WHERE id=?' + (' AND user_id=?' if owner else ''),
            (job_id, owner) if owner else (job_id,),
        ).fetchone()
        if row is None:
            raise KeyError('Job not found')
        total = db.execute('SELECT count(*) FROM job_events WHERE job_id=?', (job_id,)).fetchone()[0]
        attempts = db.execute(
            "SELECT count(*) FROM job_events WHERE job_id=? AND stage='starting'", (job_id,)
        ).fetchone()[0]
        events = db.execute(
            'SELECT id,occurred,status,stage,details FROM job_events WHERE job_id=? ORDER BY id DESC LIMIT 200',
            (job_id,),
        ).fetchall()
    return {
        'job': serialize(row, owner is not None),
        'events_total': total,
        'attempts': attempts,
        'events': [{**dict(e), 'details': json.loads(e['details'])} for e in reversed(events)],
    }
