"""Source-aware indexing and retrieval. Cached content never grants access."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path

from . import records
from .client import Cloud, CloudError, Source, in_scope, path_name
from .embeddings import Purpose
from .extraction import SUPPORTED, chunks, extract
from .search import MODES
from .store import Store

Embed = Callable[[list[str], Purpose], Awaitable[list[list[float]]]]

# A long regulation may answer a question in several places, so results are
# capped per document rather than reduced to a single passage.
MAX_PASSAGES_PER_DOCUMENT = 3
MAX_RESULTS = 8


def _supported(path: str) -> bool:
    return Path(path).suffix.lower() in SUPPORTED


class Service:
    def __init__(
        self,
        store: Store,
        base: str,
        public: str,
        embed: Embed,
        model: str,
        cloud_factory: Callable = Cloud,
        *,
        batch: int = 32,
    ) -> None:
        self.store, self.base, self.public = store, base, public.rstrip('/')
        self.embed, self.model, self.cloud_factory = embed, model, cloud_factory
        self.batch = batch

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
        self.store.enqueue(user_id, 'index', {}, origin='connection')

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
        self.store.update(job['id'], 'processing', 'discovering')
        sources = [source for source in await self.discover(cloud, account['scopes']) if _supported(source.path)]
        by_id = {source.file_id: source for source in sources}
        groups = records.group([(source.file_id, source.path) for source in sources])
        excluded = self.store.excluded(job['user_id'])
        previous = {doc['record_id']: doc for doc in self.store.documents(job['user_id'])}
        visible: set[str] = set()
        digests: dict[str, str] = {}
        count = 0
        progress = {'total': len(groups), 'processed': 0, 'indexed': 0, 'skipped': 0}
        for item in groups:
            members = [by_id[member['file_id']] for member in item.representations if member['file_id'] in by_id]
            if not members or any(member.file_id in excluded for member in members):
                progress['skipped'] += 1
                progress['processed'] += 1
                continue
            if self.store.cancelled(job['id']):
                return {'indexed': count}
            mark = records.fingerprint([{'file_id': m.file_id, 'etag': m.etag} for m in members])
            old = previous.get(item.record_id)
            if old and old['fingerprint'] == mark and old['model'] == self.model:
                # Seed duplicate detection so another folder holding identical bytes
                # still attaches to this document instead of indexing twice.
                digests.setdefault(old['digest'], item.record_id)
                visible.add(item.record_id)
                count += 1
                progress['processed'] += 1
                progress['indexed'] = count
                continue
            original = by_id[item.original_file_id]
            source = by_id.get(item.text_file_id) or original
            self.store.update(job['id'], 'processing', 'extracting', progress)
            data = await cloud.download(source.path)
            original_data = data if source is original else await cloud.download(original.path)
            digest = hashlib.sha256(original_data).hexdigest()
            known = digests.get(digest)
            if (
                known
                and known != item.record_id
                and self.store.add_representations(job['user_id'], known, item.representations)
            ):
                progress['skipped'] += 1
                progress['processed'] += 1
                continue
            text = await asyncio.to_thread(extract, source.path, data)
            parts = chunks(text)
            self.store.update(job['id'], 'processing', 'embedding', progress)
            vectors = []
            for start in range(0, len(parts), self.batch):
                vectors.extend(await self.embed(parts[start : start + self.batch], 'document'))
            if not await cloud.authorize(source) or not await cloud.authorize(original):
                raise ValueError('Source changed or access was revoked during indexing')
            self.store.put_document(
                job['user_id'],
                {
                    'file_id': source.file_id,
                    'path': source.path,
                    'etag': source.etag,
                    'digest': digest,
                    'chunks': parts,
                    'vectors': vectors,
                    'model': self.model,
                    'record_id': item.record_id,
                    'fingerprint': mark,
                    'title': item.title,
                    'original_file_id': original.file_id,
                    'original_path': original.path,
                    'original_etag': original.etag,
                    'representations': item.representations,
                },
                account['generation'],
            )
            digests[digest] = item.record_id
            visible.add(item.record_id)
            count += 1
            progress['processed'] += 1
            progress['indexed'] = count
        # Only a complete successful traversal can retire absent entries.
        for record_id in previous.keys() - visible:
            self.store.remove_record(job['user_id'], record_id)
        self.store.synced(job['user_id'])
        return progress

    async def accessible(self, cloud: Cloud, doc: dict) -> bool:
        """Both the indexed text and the cited original must be readable now."""
        if not await cloud.authorize(Source(doc['file_id'], doc['path'], doc['etag'], 0, '', False)):
            return False
        if doc['original_file_id'] == doc['file_id']:
            return True
        return await cloud.authorize(
            Source(doc['original_file_id'], doc['original_path'], doc['original_etag'], 0, '', False)
        )

    async def search(self, user_id: str, query: str, mode: str = 'hybrid') -> dict:
        if not query.strip() or len(query) > 4000:
            raise ValueError('Enter a query of 1 to 4000 characters')
        if mode not in MODES:
            raise ValueError('Unknown search mode')
        account = self.store.connection(user_id)
        async with self.cloud(account) as cloud:
            documents = await self.candidates(user_id, account, cloud)
        vector = (await self.embed([query], 'query'))[0] if mode in {'hybrid', 'semantic'} else None
        ranked = await asyncio.to_thread(self.store.search.search, user_id, documents, query, mode, vector)
        results = []
        allowed: dict[str, bool] = {}
        taken: dict[str, int] = {}
        # Recheck the selected results after ranking to cover changes during a search.
        async with self.cloud(account) as cloud:
            for match in ranked:
                doc = match['doc']
                if taken.get(doc['record_id'], 0) >= MAX_PASSAGES_PER_DOCUMENT:
                    continue
                if doc['record_id'] not in allowed:
                    allowed[doc['record_id']] = await self.accessible(cloud, doc)
                if not allowed[doc['record_id']]:
                    self.store.remove_document(user_id, doc['file_id'])
                    continue
                taken[doc['record_id']] = taken.get(doc['record_id'], 0) + 1
                results.append(self.hit(doc, match, mode))
                if len(results) == MAX_RESULTS:
                    break
        if self.store.connection(user_id)['generation'] != account['generation']:
            self.store.search.invalidate(user_id)
            raise ValueError('Account connection changed')
        return {'sources': results}

    async def candidates(self, user_id: str, account: dict, cloud: Cloud) -> list[dict]:
        """Indexed documents that are still authorized, dropping the ones that are not."""
        documents = []
        for doc in self.store.documents(user_id):
            if doc['model'] != self.model or not in_scope(doc['path'], account['scopes']):
                continue
            if await self.accessible(cloud, doc):
                documents.append(doc)
            else:
                self.store.remove_document(user_id, doc['file_id'])
        return documents

    def hit(self, doc: dict, match: dict, mode: str) -> dict:
        return {
            'file_id': doc['file_id'],
            'document_id': doc['record_id'],
            'title': doc['title'],
            'path': doc['path'],
            'url': self.link(doc['original_file_id']),
            'text': match['text'],
            'score': match['score'],
            'search_mode': mode,
            'revision': doc['etag'],
            'passage': match['chunk'],
            'character_start': (match['chunk'] - 1) * 1400,
            'character_end': (match['chunk'] - 1) * 1400 + len(match['text']),
            'representations': [
                {
                    'file_id': entry['file_id'],
                    'path': entry['path'],
                    'kind': entry['kind'],
                    'url': self.link(entry['file_id']),
                }
                for entry in doc['representations']
            ],
        }

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
