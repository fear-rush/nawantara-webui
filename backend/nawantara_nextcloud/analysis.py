"""Bounded private analysis over deliberately selected, currently authorized inputs."""

import base64
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx


async def run(api, user_id: str, paths: list[str], code: str) -> dict:
    if not 1 <= len(paths) <= 10 or not 1 <= len(code) <= 40000:
        raise ValueError('Choose one to ten files and 1 to 40000 characters of Python')
    account = api.store.connection(user_id)
    sources, files = [], {}
    size = 0
    async with api.cloud(account) as cloud:
        for path in paths:
            source = await cloud.stat(path)
            name = Path(source.path).name
            if name in files or name in {'analysis.py', 'output'}:
                raise ValueError('Selected filenames must be distinct and cannot be analysis.py or output')
            if source.folder or not await cloud.authorize(source):
                raise ValueError('Selected source is unavailable')
            data = await cloud.download(source.path)
            size += len(data)
            if size > 20 * 1024 * 1024:
                raise ValueError('Selected inputs exceed 20 MiB')
            files[name] = base64.b64encode(data).decode()
            sources.append(source)
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as client:
        response = await client.post(
            os.environ['ANALYSIS_URL'] + '/execute',
            headers={'Authorization': 'Bearer ' + os.environ['ANALYSIS_SECRET']},
            json={'code': code, 'files': files},
        )
        if response.status_code != 200:
            raise ValueError('Analysis failed or exceeded its resource limits')
        result = response.json()
    async with api.cloud(account) as cloud:
        for source in sources:
            if not await cloud.authorize(source):
                raise ValueError('A selected source changed or access was revoked')
    if api.store.connection(user_id)['generation'] != account['generation']:
        raise ValueError('Account connection changed')
    run_id = uuid.uuid4().hex
    with api.store.db() as db:
        db.execute(
            'INSERT INTO analysis_runs VALUES (?,?,?,?)',
            (
                run_id,
                user_id,
                api.store.cipher.encrypt(json.dumps(result).encode()),
                time.time(),
            ),
        )
    return {
        'run_id': run_id,
        'exit_code': result['exit_code'],
        'output': result['output'],
        'files': [
            {
                'name': item['name'],
                'url': '/api/v1/nextcloud/analysis/' + run_id + '/files/' + quote(item['name'], safe=''),
            }
            for item in result['files']
        ],
        'sources': [{'url': api.link(source.file_id), 'revision': source.etag} for source in sources],
    }


def file_bytes(store, user_id: str, run_id: str, name: str) -> bytes:
    with store.db() as db:
        row = db.execute('SELECT result FROM analysis_runs WHERE id=? AND user_id=?', (run_id, user_id)).fetchone()
    if row is None:
        raise ValueError('Analysis file unavailable')
    result = json.loads(store.cipher.decrypt(row[0]))
    for item in result['files']:
        if item['name'] == name:
            return base64.b64decode(item['data'], validate=True)
    raise ValueError('Analysis file unavailable')
