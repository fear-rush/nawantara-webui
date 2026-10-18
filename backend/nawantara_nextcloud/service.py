"""Source-aware indexing and retrieval. Cached content never grants access."""

from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Awaitable, Callable
from pathlib import Path

from .client import Cloud, CloudError, Source, in_scope, path_name
from .embeddings import Purpose
from .extraction import SUPPORTED, chunks, extract
from .store import Store

Embed = Callable[[list[str], Purpose], Awaitable[list[list[float]]]]


class Service:
    def __init__(
        self, store: Store, base: str, public: str, embed: Embed, model: str, cloud_factory: Callable = Cloud
    ) -> None:
        self.store, self.base, self.public = store, base, public.rstrip('/')
        self.embed, self.model, self.cloud_factory = embed, model, cloud_factory

    def cloud(self, account: dict) -> Cloud:
        if account['instance'] != self.base:
            raise ValueError('Nextcloud server changed. Reconnect your account.')
        return self.cloud_factory(self.base, account['username'], account['password'])

    def link(self, file_id: str) -> str:
        from urllib.parse import quote

        return self.public + '/index.php/f/' + quote(file_id, safe='')

    async def connect(self, user_id: str, username: str, password: str, scopes: list[str]) -> None:
        scopes = sorted(set(path_name(scope) for scope in scopes))
        if not scopes or len(scopes) > 20:
            raise ValueError('Choose between one and twenty indexing folders')
        async with self.cloud_factory(self.base, username, password) as cloud:
            identity = await cloud.identity()
        async with self.cloud_factory(self.base, identity, password) as cloud:
            for scope in scopes:
                if not (await cloud.stat(scope)).folder:
                    raise ValueError('Indexing scopes must be folders')
        self.store.connect(user_id, identity, password, scopes, self.base)
        self.store.enqueue(user_id, 'index', {})

    async def discover(self, cloud: Cloud, scopes: list[str]) -> list[Source]:
        pending = list(scopes)
        visited = set()
        sources = {}
        while pending:
            folder = pending.pop()
            if folder in visited:
                continue
            visited.add(folder)
            if len(visited) + len(sources) > 10000:
                raise ValueError('Indexing scope exceeds 10000 entries')
            for source in await cloud.listing(folder):
                if len(visited) + len(sources) >= 10000:
                    raise ValueError('Indexing scope exceeds 10000 entries')
                if not in_scope(source.path, scopes):
                    continue
                if source.folder and source.path not in visited:
                    pending.append(source.path)
                elif not source.folder:
                    sources[source.file_id] = source
        return list(sources.values())

    async def index(self, job: dict, account: dict, cloud: Cloud) -> dict:
        sources = await self.discover(cloud, account['scopes'])
        previous = {d['file_id']: d for d in self.store.documents(job['user_id'])}
        visible = {source.file_id for source in sources}
        count = 0
        for source in sources:
            if source.file_id in self.store.excluded(job['user_id']):
                continue
            if self.store.cancelled(job['id']):
                return {'indexed': count}
            if Path(source.path).suffix.lower() not in SUPPORTED:
                continue
            old = previous.get(source.file_id)
            if old and old['etag'] == source.etag and old['path'] == source.path and old['model'] == self.model:
                count += 1
                continue
            self.store.update(job['id'], 'processing', 'extracting')
            data = await cloud.download(source.path)
            text = await asyncio.to_thread(extract, source.path, data)
            parts = chunks(text)
            self.store.update(job['id'], 'processing', 'embedding')
            vectors = []
            for start in range(0, len(parts), 32):
                vectors.extend(await self.embed(parts[start : start + 32], 'document'))
            if not await cloud.authorize(source):
                raise ValueError('Source changed or access was revoked during indexing')
            self.store.put_document(
                job['user_id'],
                {
                    'file_id': source.file_id,
                    'path': source.path,
                    'etag': source.etag,
                    'digest': hashlib.sha256(data).hexdigest(),
                    'chunks': parts,
                    'vectors': vectors,
                    'model': self.model,
                },
                account['generation'],
            )
            count += 1
        # Only a complete successful traversal can retire absent entries.
        for file_id in previous.keys() - visible:
            self.store.remove_document(job['user_id'], file_id)
        self.store.synced(job['user_id'])
        return {'indexed': count}

    async def search(self, user_id: str, query: str) -> dict:
        if not query.strip() or len(query) > 4000:
            raise ValueError('Enter a query of 1 to 4000 characters')
        account = self.store.connection(user_id)
        vector = (await self.embed([query], 'query'))[0]
        candidates = []
        for doc in self.store.documents(user_id):
            if doc['model'] != self.model or not in_scope(doc['path'], account['scopes']):
                continue
            for text, embedding in zip(doc['chunks'], doc['vectors'], strict=True):
                score = similarity(vector, embedding)
                candidates.append((score, doc, text))
        candidates.sort(key=lambda item: item[0], reverse=True)
        results = []
        checked = {}
        async with self.cloud(account) as cloud:
            for score, doc, text in candidates:
                file_id = doc['file_id']
                if file_id not in checked:
                    checked[file_id] = await cloud.authorize(Source(file_id, doc['path'], doc['etag'], 0, '', False))
                if checked[file_id]:
                    results.append(
                        {
                            'file_id': file_id,
                            'path': doc['path'],
                            'url': self.link(file_id),
                            'text': text,
                            'score': score,
                        }
                    )
                else:
                    self.store.remove_document(user_id, file_id)
                if len(results) == 8:
                    break
        if self.store.connection(user_id)['generation'] != account['generation']:
            raise ValueError('Account connection changed')
        return {'sources': results}

    async def browse(self, user_id: str, path: str) -> list[dict]:
        account = self.store.connection(user_id)
        async with self.cloud(account) as cloud:
            entries = await cloud.listing(path_name(path))
            results = []
            for source in entries:
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
            data = await cloud.download(source.path)
            text = await asyncio.to_thread(extract, source.path, data)
            if not await cloud.authorize(source):
                raise ValueError('Source changed or is no longer accessible')
            return {
                'file_id': source.file_id,
                'path': source.path,
                'url': self.link(source.file_id),
                'text': text[:30000],
                'truncated': len(text) > 30000,
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


def similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        return -1.0
    norm = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / norm if norm else 0.0
