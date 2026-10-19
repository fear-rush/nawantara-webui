"""Source-aware browsing, text reading and conditional saving.

Nextcloud owns files, sharing and revisions. This service only lists what the
connected user may see, reads plain text, and creates new files on request.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

from .client import Cloud, CloudError, path_name
from .store import Store

TEXT_SUFFIXES = {'.txt', '.md', '.csv', '.json', '.log', '.yaml', '.yml'}
MAX_TEXT_CHARACTERS = 2_000_000
RETURNED_TEXT_CHARACTERS = 30_000


class Service:
    def __init__(self, store: Store, base: str, public: str, cloud_factory: Callable = Cloud) -> None:
        self.store, self.base, self.public = store, base, public.rstrip('/')
        self.cloud_factory = cloud_factory

    def cloud(self, account: dict) -> Cloud:
        if account['instance'] != self.base:
            raise ValueError('Nextcloud server changed. Reconnect your account.')
        return self.cloud_factory(self.base, account['username'], account['password'])

    def link(self, file_id: str) -> str:
        from urllib.parse import quote

        return self.public + '/index.php/f/' + quote(file_id, safe='')

    async def connect(self, user_id: str, username: str, password: str) -> None:
        async with self.cloud_factory(self.base, username, password) as cloud:
            identity = await cloud.identity()
        async with self.cloud_factory(self.base, identity, password) as cloud:
            await cloud.stat('/')
        self.store.connect(user_id, identity, password, self.base)

    async def browse(self, user_id: str, path: str) -> list[dict]:
        account = self.store.connection(user_id)
        async with self.cloud(account) as cloud:
            entries = await cloud.listing(path_name(path))
            results = []
            for source in entries:
                if source.path == path_name(path):
                    continue
                if source.folder or await cloud.authorize(source):
                    results.append(
                        {
                            'file_id': source.file_id,
                            'path': source.path,
                            'folder': source.folder,
                            'url': self.link(source.file_id),
                        }
                    )
            return results

    async def read(self, user_id: str, path: str) -> dict:
        account = self.store.connection(user_id)
        async with self.cloud(account) as cloud:
            source = await cloud.stat(path_name(path))
            if source.folder:
                raise ValueError('Choose a file to read')
            if Path(source.path).suffix.lower() not in TEXT_SUFFIXES:
                raise ValueError('Only text files can be read here. Open other formats in Nextcloud.')
            data = await cloud.download(source.path)
            if not await cloud.authorize(source):
                raise ValueError('Source changed or is no longer accessible')
        text = data.decode('utf-8-sig', errors='replace')
        if len(text) > MAX_TEXT_CHARACTERS:
            raise ValueError('File exceeds the text reading limit')
        return {
            'file_id': source.file_id,
            'path': source.path,
            'url': self.link(source.file_id),
            'revision': source.etag,
            'text': text[:RETURNED_TEXT_CHARACTERS],
            'truncated': len(text) > RETURNED_TEXT_CHARACTERS,
        }

    async def save(self, job: dict, cloud: Cloud, data: bytes) -> dict:
        account = self.store.connection(job['user_id'])
        if account['generation'] != job['payload']['generation']:
            raise ValueError('Account connection changed')
        path = path_name(job['payload']['path'])
        if path == '/':
            raise ValueError('Choose a filename')
        result = job['result']
        digest = hashlib.sha256(data).hexdigest()
        if not result.get('saved'):
            # Conditional creation prevents overwriting a concurrent or unrelated file.
            uncertain = result.get('write_started', False)
            self.store.update(job['id'], 'processing', 'saving', {'write_started': True})
            try:
                await cloud.create(path, data)
            except CloudError as error:
                if error.status != 412 or not uncertain:
                    if error.status < 500:
                        self.store.update(job['id'], 'processing', 'write_rejected', {'write_started': False})
                    raise
                if hashlib.sha256(await cloud.download(path)).hexdigest() != digest:
                    raise ValueError('Destination contains a different file') from None
            source = await cloud.stat(path)
            result = {'saved': True, 'file_id': source.file_id, 'url': self.link(source.file_id)}
            self.store.update(job['id'], 'processing', 'saved', result)
        return result
