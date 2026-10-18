"""One backend-owned executor and application adapters."""

from __future__ import annotations

import asyncio
import fcntl
import logging
import os
from pathlib import Path

from fastapi import FastAPI

from .service import Service
from .store import Store

log = logging.getLogger(__name__)


def store(app: FastAPI) -> Store:
    return app.state.nextcloud_store


async def service(app: FastAPI) -> Service:
    from open_webui.models.config import Config

    config = await Config.get_many('rag.embedding_engine', 'rag.embedding_model')
    model = str(config['rag.embedding_engine']) + ':' + str(config['rag.embedding_model'])

    async def embed(texts: list[str]) -> list[list[float]]:
        return await app.state.EMBEDDING_FUNCTION(texts)

    return Service(store(app), os.environ['NEXTCLOUD_INTERNAL_URL'], os.environ['NEXTCLOUD_PUBLIC_URL'], embed, model)


async def attachment(user_id: str, file_id: str) -> bytes:
    from open_webui.models.files import Files
    from open_webui.storage.provider import Storage

    from .client import MAX_BYTES

    file = await Files.get_file_by_id_and_user_id(file_id, user_id)
    if file is None:
        raise ValueError('Attachment unavailable or not owned by this account')
    path = Path(await asyncio.to_thread(Storage.get_file, file.path))
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Attachment exceeds 50 MiB')
    return await asyncio.to_thread(path.read_bytes)


async def execute(app: FastAPI, job: dict) -> None:
    from open_webui.models.users import Users

    api = await service(app)
    db = api.store
    try:
        user = await Users.get_user_by_id(job['user_id'])
        if user is None or user.role not in {'admin', 'user'}:
            raise ValueError('Account is unavailable')
        account = db.connection(job['user_id'])
        if account['generation'] != job['payload']['generation']:
            raise ValueError('Account connection changed; submit a new job')
        if db.cancelled(job['id']):
            db.update(job['id'], 'cancelled', 'cancelled')
            return
        async with api.cloud(account) as cloud:
            if job['action'] == 'index':
                result = await api.index(job, account, cloud)
            else:
                payload = job['payload']
                data = (
                    await attachment(job['user_id'], payload['file_id'])
                    if payload.get('file_id')
                    else payload['text'].encode()
                )
                result = await api.save(job, cloud, data)
                if not db.cancelled(job['id']):
                    result['index_job_id'] = db.enqueue(job['user_id'], 'index', {})
            cancelled = db.cancelled(job['id'])
            db.update(
                job['id'], 'cancelled' if cancelled else 'completed', 'cancelled' if cancelled else 'completed', result
            )
    except Exception as error:
        # Diagnostic types are safe; exception bodies can include document text or credentials.
        log.warning('Nextcloud job %s failed (%s)', job['id'], type(error).__name__)
        db.update(
            job['id'],
            'failed',
            'failed',
            error=(
                'Processing failed. Check the connection, source access, file format and destination. '
                'Retry reconciles the saved checkpoint.'
            ),
        )


async def run(app: FastAPI) -> None:
    lock_path = store(app).path.with_suffix('.lock')
    with lock_path.open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        store(app).interrupt()
        while True:
            for user_id in store(app).due_accounts():
                store(app).enqueue(user_id, 'index', {})
                store(app).synced(user_id)
            job = store(app).claim()
            if job:
                await execute(app, job)
            else:
                await asyncio.sleep(2)


def start(app: FastAPI) -> asyncio.Task:
    app.state.nextcloud_store = Store(
        Path(os.environ.get('DATA_DIR', '/app/backend/data')), os.environ['NEXTCLOUD_CREDENTIAL_KEY']
    )
    return asyncio.create_task(run(app))
