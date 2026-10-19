"""Documents are grouped before indexing, and only one text per document is indexed."""

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Self

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nawantara_nextcloud.client import Source
from nawantara_nextcloud.service import Service
from nawantara_nextcloud.store import Store


async def embed(texts: list[str], purpose: str) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


class TreeCloud:
    """A Nextcloud tree that serves fixed bytes per path."""

    def __init__(self, entries: dict[str, tuple[str, str, bytes]]) -> None:
        self.entries = entries
        self.allowed: set[str] | None = None
        self.downloads: list[str] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def listing(self, path: str) -> list[Source]:
        root = path.rstrip('/')
        found = []
        directories = set()
        for file_id, (name, etag, data) in sorted(self.entries.items()):
            parent = str(Path(name).parent)
            if parent == path:
                found.append(Source(file_id, name, etag, len(data), 'application/octet-stream', False))
            elif parent.startswith(root + '/'):
                directories.add(root + '/' + parent[len(root) + 1 :].split('/')[0])
        for directory in sorted(directories):
            found.append(Source('folder:' + directory, directory, '', 0, 'httpd/unix-directory', True))
        return found

    async def download(self, path: str) -> bytes:
        self.downloads.append(path)
        for name, _etag, data in self.entries.values():
            if name == path:
                return data
        raise AssertionError(f'unexpected download {path}')

    async def authorize(self, source: Source) -> bool:
        if source.file_id not in self.entries:
            return False
        return self.allowed is None or source.file_id in self.allowed


class DocumentIndexing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name), Fernet.generate_key().decode())
        self.store.connect('alice', 'alice', 'secret', ['/Company'], 'http://cloud')
        self.api = Service(self.store, 'http://cloud', 'https://cloud.example', embed, 'local:model', self.factory)
        self.cloud = TreeCloud({})

    def factory(self, base: str, username: str, password: str) -> TreeCloud:
        return self.cloud

    def tearDown(self) -> None:
        self.directory.cleanup()

    async def index(self) -> dict:
        job_id = self.store.enqueue('alice', 'index', {})
        job = self.store.claim()
        assert job is not None and job['id'] == job_id
        result = await self.api.index(job, self.store.connection('alice'), self.cloud)
        self.store.update(job['id'], 'completed', 'completed', result)
        return result

    async def test_markdown_is_indexed_and_structure_json_is_not(self) -> None:
        self.cloud.entries = {
            '1': ('/Company/Reg.pdf', 'e1', b'%PDF-1.4 original bytes'),
            '2': ('/Company/MinerU_markdown_Reg_2096144854990213120.md', 'e2', b'Pasal 1 on consumer duties'),
            '3': ('/Company/MinerU_Reg__20260905075409.json', 'e3', b'STRUCTURE-ONLY-MARKER'),
        }
        progress = await self.index()
        self.assertEqual(progress['indexed'], 1)
        documents = self.store.documents('alice')
        self.assertEqual(len(documents), 1)
        document = documents[0]
        self.assertEqual(document['record_id'], '1')
        self.assertEqual(document['file_id'], '2')
        self.assertEqual(document['title'], 'Reg')
        self.assertEqual(len(document['representations']), 3)
        text = '\n'.join(document['chunks'])
        self.assertIn('consumer duties', text)
        self.assertNotIn('STRUCTURE-ONLY-MARKER', text)
        self.assertNotIn('/Company/MinerU_Reg__20260905075409.json', self.cloud.downloads)

    async def test_identical_publication_in_another_folder_attaches(self) -> None:
        self.cloud.entries = {
            '1': ('/Company/A/Reg.pdf', 'e1', b'IDENTICAL-BYTES'),
            '2': ('/Company/A/MinerU_markdown_Reg_1000000000000000001.md', 'e2', b'text from folder A'),
            '3': ('/Company/B/Reg.pdf', 'e3', b'IDENTICAL-BYTES'),
            '4': ('/Company/B/MinerU_markdown_Reg_1000000000000000002.md', 'e4', b'text from folder B'),
        }
        await self.index()
        documents = self.store.documents('alice')
        self.assertEqual(len(documents), 1)
        self.assertEqual({item['file_id'] for item in documents[0]['representations']}, {'1', '2', '3', '4'})

    async def test_search_cites_the_original_and_lists_representations(self) -> None:
        self.cloud.entries = {
            '1': ('/Company/Reg.pdf', 'e1', b'%PDF-1.4 original bytes'),
            '2': ('/Company/MinerU_markdown_Reg_1000000000000000001.md', 'e2', b'consumer protection duties'),
            '3': ('/Company/MinerU_Reg__20260905075409.json', 'e3', b'{}'),
        }
        await self.index()
        result = await self.api.search('alice', 'consumer')
        self.assertEqual(len(result['sources']), 1)
        hit = result['sources'][0]
        self.assertEqual(hit['url'], 'https://cloud.example/index.php/f/1')
        self.assertEqual(hit['document_id'], '1')
        self.assertEqual(hit['title'], 'Reg')
        self.assertEqual(len(hit['representations']), 3)

    async def test_losing_access_to_the_original_blocks_the_passage(self) -> None:
        self.cloud.entries = {
            '1': ('/Company/Reg.pdf', 'e1', b'%PDF-1.4 original bytes'),
            '2': ('/Company/MinerU_markdown_Reg_1000000000000000001.md', 'e2', b'consumer protection duties'),
        }
        await self.index()
        self.cloud.allowed = {'2'}
        result = await self.api.search('alice', 'consumer')
        self.assertEqual(result, {'sources': []})
        self.assertEqual(self.store.documents('alice'), [])

    async def test_removing_a_representation_excludes_the_document(self) -> None:
        self.cloud.entries = {
            '1': ('/Company/Reg.pdf', 'e1', b'%PDF-1.4 original bytes'),
            '2': ('/Company/MinerU_markdown_Reg_1000000000000000001.md', 'e2', b'consumer protection duties'),
        }
        await self.index()
        self.store.exclude('alice', '1')
        await self.index()
        self.assertEqual(self.store.documents('alice'), [])

    def test_index_shape_change_rebuilds_derived_data_without_touching_sources(self) -> None:
        path = Path(self.directory.name) / 'nextcloud.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute('DROP TABLE documents')
            db.execute(
                'CREATE TABLE documents (user_id TEXT, file_id TEXT, path TEXT, etag TEXT, digest TEXT,'
                ' chunks TEXT, vectors TEXT, model TEXT, PRIMARY KEY(user_id,file_id))'
            )
            db.execute("INSERT INTO documents VALUES ('alice','1','/p','e','d','[]','[]','m')")
        Store(Path(self.directory.name), Fernet.generate_key().decode())
        with sqlite3.connect(path) as db:
            columns = [row[1] for row in db.execute('PRAGMA table_info(documents)')]
            self.assertIn('record_id', columns)
            self.assertEqual(db.execute('SELECT count(*) FROM documents').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT synced FROM connections WHERE user_id='alice'").fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
