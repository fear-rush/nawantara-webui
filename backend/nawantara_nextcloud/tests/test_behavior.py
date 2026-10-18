"""Privacy, conditional writes and resumable indexing in the WebUI integration."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Self

import httpx
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nawantara_nextcloud.client import Cloud, CloudError, Source, in_scope, path_name
from nawantara_nextcloud.service import Service
from nawantara_nextcloud.store import Store


async def embed(texts: list[str], purpose: str) -> list[list[float]]:
    return [[1.0, 0.0] for _ in texts]


class AccessCloud:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.creates = 0

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def authorize(self, source: Source) -> bool:
        return self.allowed

    async def create(self, path: str, data: bytes) -> None:
        self.creates += 1
        raise CloudError(412)

    async def download(self, path: str) -> bytes:
        return b'selected finding'

    async def stat(self, path: str) -> Source:
        return Source('42', path, 'etag', 16, 'text/plain', False)


class NextcloudBehavior(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name), Fernet.generate_key().decode())
        self.store.connect('alice', 'alice', 'private-application-secret', ['/Company'], 'http://cloud')
        self.store.connect('bob', 'bob', 'different-secret', ['/Company'], 'http://cloud')
        self.cloud = AccessCloud(True)
        self.api = Service(
            self.store,
            'http://cloud',
            'https://cloud.example',
            embed,
            'local:model',
            self.cloud_factory,
        )
        self.document('alice')

    def cloud_factory(self, base: str, username: str, password: str) -> AccessCloud:
        return self.cloud

    def tearDown(self) -> None:
        self.directory.cleanup()

    def document(self, user_id: str) -> None:
        self.store.put_document(
            user_id,
            {
                'file_id': '42',
                'path': '/Company/private.txt',
                'etag': 'etag',
                'digest': 'hash',
                'chunks': ['restricted payroll passage'],
                'vectors': [[1.0, 0.0]],
                'model': 'local:model',
            },
            self.store.connection(user_id)['generation'],
        )

    async def test_revocation_blocks_cached_passages_and_metadata(self) -> None:
        self.cloud.allowed = False
        result = await self.api.search('alice', 'payroll')
        self.assertEqual(result, {'sources': []})
        self.assertEqual(self.store.documents('alice'), [])

    async def test_other_user_cannot_query_private_index(self) -> None:
        self.assertEqual(await self.api.search('bob', 'payroll'), {'sources': []})

    async def test_authorized_results_include_source_link(self) -> None:
        result = await self.api.search('alice', 'payroll')
        self.assertEqual(result['sources'][0]['url'], 'https://cloud.example/index.php/f/42')

    async def test_saved_checkpoint_does_not_repeat_write(self) -> None:
        job_id = self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        job = self.store.claim()
        job['result'] = {'saved': True, 'file_id': '42'}
        await self.api.save(job, self.cloud, b'selected finding')
        self.assertEqual(self.cloud.creates, 0)
        self.assertEqual(job['id'], job_id)

    async def test_uncertain_write_matches_bytes_before_reusing(self) -> None:
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        job = self.store.claim()
        job['result'] = {'write_started': True}
        result = await self.api.save(job, self.cloud, b'selected finding')
        self.assertTrue(result['saved'])

    async def test_existing_file_never_overwritten(self) -> None:
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        job = self.store.claim()
        with self.assertRaises(CloudError):
            await self.api.save(job, self.cloud, b'selected finding')

    async def test_uncertain_different_content_requires_resolution(self) -> None:
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        job = self.store.claim()
        job['result'] = {'write_started': True}
        with self.assertRaises(ValueError):
            await self.api.save(job, self.cloud, b'different file')

    async def test_rejected_creation_is_not_treated_as_an_uncertain_write(self) -> None:
        job_id = self.store.enqueue('alice', 'save', {'path': '/Company/existing.txt'})
        job = self.store.claim()
        with self.assertRaises(CloudError):
            await self.api.save(job, self.cloud, b'selected finding')
        result = next(row for row in self.store.jobs('alice') if row['id'] == job_id)['result']
        self.assertFalse(result['write_started'])

    async def test_changed_server_cannot_reuse_a_connection_or_cached_index(self) -> None:
        other = Service(
            self.store, 'http://another-cloud', 'https://other.example', embed, 'local:model', self.cloud_factory
        )
        with self.assertRaises(ValueError):
            await other.search('alice', 'payroll')

    def test_connection_rotation_rejects_inflight_index_write(self) -> None:
        old = self.store.connection('alice')['generation']
        self.store.connect('alice', 'replacement', 'new-secret', ['/Company'], 'http://cloud')
        with self.assertRaises(ValueError):
            self.store.put_document('alice', {}, old)

    def test_disconnect_removes_credentials_index_and_jobs(self) -> None:
        self.store.enqueue('alice', 'index', {})
        self.store.disconnect('alice')
        self.assertEqual(self.store.documents('alice'), [])
        self.assertEqual(self.store.jobs('alice'), [])
        with self.assertRaises(ValueError):
            self.store.connection('alice')

    def test_job_claim_is_atomic_across_connections(self) -> None:
        self.store.enqueue('alice', 'index', {})
        other = Store(Path(self.directory.name), Fernet.generate_key().decode())
        self.assertIsNotNone(self.store.claim())
        self.assertIsNone(other.claim())

    def test_admin_job_feed_has_no_payload_or_source(self) -> None:
        job_id = self.store.enqueue('alice', 'save', {'path': '/Company/payroll.txt', 'text': 'private'})
        self.store.update(job_id, 'completed', 'completed', {'url': 'https://cloud.example/private'})
        data = self.store.jobs(None)
        self.assertEqual(data[0]['result'], {})
        self.assertNotIn('private', str(data))
        self.assertNotIn('payroll', str(data))

    def test_application_password_is_encrypted_on_disk(self) -> None:
        self.assertNotIn(b'private-application-secret', self.store.path.read_bytes())

    def test_index_exclusion_does_not_call_source_deletion(self) -> None:
        self.store.exclude('alice', '42')
        self.assertEqual(self.store.documents('alice'), [])
        self.assertEqual(self.store.excluded('alice'), {'42'})
        self.assertEqual(self.cloud.creates, 0)

    def test_user_cannot_cancel_another_users_job(self) -> None:
        job_id = self.store.enqueue('alice', 'index', {})
        with self.assertRaises(ValueError):
            self.store.control(job_id, 'bob', 'cancel')
        self.assertFalse(self.store.cancelled(job_id))


class DAVBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_conditional_creation_prevents_overwrite(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers['If-None-Match'], '*')
            self.assertEqual(request.url.path, '/remote.php/dav/files/alice/Company/new.txt')
            return httpx.Response(412)

        async with Cloud('https://cloud.example', 'alice', 'secret', httpx.MockTransport(respond)) as cloud:
            with self.assertRaises(CloudError):
                await cloud.create('/Company/new.txt', b'new')

    def test_scope_boundary_cannot_match_similar_folder_name(self) -> None:
        self.assertTrue(in_scope('/Company/file', ['/Company']))
        self.assertFalse(in_scope('/CompanyPrivate/file', ['/Company']))
        with self.assertRaises(ValueError):
            path_name('/Company/../Private/file')
