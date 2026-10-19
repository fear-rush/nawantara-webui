"""Privacy, conditional writes and resumable saves in the WebUI integration."""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Self

import httpx
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nawantara_nextcloud.client import Cloud, CloudError, Source, path_name
from nawantara_nextcloud.service import Service
from nawantara_nextcloud.store import Store


class AccessCloud:
    """A Nextcloud tree that serves fixed bytes and can deny access."""

    def __init__(self, entries: dict | None = None, allowed: bool = True, existing: bytes = b'selected finding'):
        self.entries = entries or {}
        self.allowed = allowed
        self.existing = existing
        self.creates = 0
        self.conflict = True

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def identity(self) -> str:
        return 'alice'

    async def listing(self, path: str) -> list[Source]:
        return [
            Source(file_id, name, 'etag', len(data), 'text/plain', False)
            for file_id, (name, _etag, data) in sorted(self.entries.items())
            if str(Path(name).parent) == path
        ]

    async def stat(self, path: str) -> Source:
        for file_id, (name, _etag, data) in self.entries.items():
            if name == path:
                return Source(file_id, name, 'etag', len(data), 'text/plain', False)
        raise CloudError(404)

    async def download(self, path: str) -> bytes:
        for _file_id, (name, _etag, data) in self.entries.items():
            if name == path:
                return data
        return self.existing

    async def authorize(self, source: Source) -> bool:
        return self.allowed and source.file_id in self.entries

    async def create(self, path: str, data: bytes) -> None:
        self.creates += 1
        if self.conflict:
            raise CloudError(412)
        self.entries['new'] = (path, 'etag', data)


class NextcloudBehavior(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name), Fernet.generate_key().decode())
        self.store.connect('alice', 'alice', 'private-application-secret', 'http://cloud')
        self.store.connect('bob', 'bob', 'different-secret', 'http://cloud')
        self.cloud = AccessCloud()
        self.api = Service(self.store, 'http://cloud', 'https://cloud.example', self.cloud_factory)

    def cloud_factory(self, base: str, username: str, password: str) -> AccessCloud:
        return self.cloud

    def tearDown(self) -> None:
        self.directory.cleanup()

    async def save(self) -> dict:
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt', 'text': 'selected finding'})
        job = self.store.claim()
        return await self.api.save(job, self.cloud, b'selected finding')

    async def test_saved_checkpoint_does_not_repeat_write(self) -> None:
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        job = self.store.claim()
        job['result'] = {'saved': True, 'file_id': '42'}
        result = await self.api.save(job, self.cloud, b'selected finding')
        self.assertEqual(self.cloud.creates, 0)
        self.assertTrue(result['saved'])

    async def test_uncertain_write_matches_bytes_before_reusing(self) -> None:
        self.cloud.entries['99'] = ('/Company/new.txt', 'etag', b'selected finding')
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        job = self.store.claim()
        job['result'] = {'write_started': True}
        result = await self.api.save(job, self.cloud, b'selected finding')
        self.assertEqual(result['file_id'], '99')
        self.assertTrue(result['saved'])

    async def test_uncertain_different_content_requires_resolution(self) -> None:
        self.cloud.entries['99'] = ('/Company/new.txt', 'etag', b'different file')
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        job = self.store.claim()
        job['result'] = {'write_started': True}
        with self.assertRaises(ValueError):
            await self.api.save(job, self.cloud, b'selected finding')

    async def test_existing_file_never_overwritten(self) -> None:
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        job = self.store.claim()
        with self.assertRaises(CloudError):
            await self.api.save(job, self.cloud, b'selected finding')

    async def test_rejected_creation_is_not_treated_as_an_uncertain_write(self) -> None:
        job_id = self.store.enqueue('alice', 'save', {'path': '/Company/existing.txt'})
        job = self.store.claim()
        with self.assertRaises(CloudError):
            await self.api.save(job, self.cloud, b'selected finding')
        result = next(row for row in self.store.jobs('alice') if row['id'] == job_id)['result']
        self.assertFalse(result['write_started'])

    async def test_successful_save_returns_a_source_link(self) -> None:
        self.cloud.conflict = False
        result = await self.save()
        self.assertEqual(result['url'], 'https://cloud.example/index.php/f/new')

    async def test_other_users_private_attachment_cannot_be_saved(self) -> None:
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        job = self.store.claim()
        job['user_id'] = 'bob'
        with self.assertRaises(ValueError):
            await self.api.save(job, self.cloud, b'selected finding')

    def test_connection_rotation_rejects_inflight_write(self) -> None:
        old = self.store.connection('alice')['generation']
        self.store.connect('alice', 'replacement', 'new-secret', 'http://cloud')
        self.assertNotEqual(old, self.store.connection('alice')['generation'])

    def test_disconnect_removes_credentials_and_jobs(self) -> None:
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        self.store.disconnect('alice')
        self.assertEqual(self.store.jobs('alice'), [])
        with self.assertRaises(ValueError):
            self.store.connection('alice')

    def test_job_claim_is_atomic_across_connections(self) -> None:
        self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        other = Store(Path(self.directory.name), Fernet.generate_key().decode())
        self.assertIsNotNone(self.store.claim())
        self.assertIsNone(other.claim())

    def test_application_password_is_encrypted_on_disk(self) -> None:
        self.assertNotIn(b'private-application-secret', self.store.path.read_bytes())

    def test_user_cannot_cancel_another_users_job(self) -> None:
        job_id = self.store.enqueue('alice', 'save', {'path': '/Company/new.txt'})
        with self.assertRaises(ValueError):
            self.store.control(job_id, 'bob', 'cancel')
        self.assertFalse(self.store.cancelled(job_id))


class ReadingText(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name), Fernet.generate_key().decode())
        self.store.connect('alice', 'alice', 'secret', 'http://cloud')
        self.cloud = AccessCloud(
            {
                '1': ('/Company/notes.md', 'etag', b'# Notes\nleave policy'),
                '2': ('/Company/scan.pdf', 'etag', b'%PDF-1.4 binary'),
                '3': ('/Company/large.txt', 'etag', b'x' * 40000),
            }
        )
        self.api = Service(self.store, 'http://cloud', 'https://cloud.example', self.cloud_factory)

    def cloud_factory(self, base: str, username: str, password: str) -> AccessCloud:
        return self.cloud

    def tearDown(self) -> None:
        self.directory.cleanup()

    async def test_text_file_is_returned_with_a_source_link(self) -> None:
        result = await self.api.read('alice', '/Company/notes.md')
        self.assertIn('leave policy', result['text'])
        self.assertEqual(result['url'], 'https://cloud.example/index.php/f/1')
        self.assertFalse(result['truncated'])

    async def test_binary_format_is_refused_instead_of_decoded(self) -> None:
        with self.assertRaises(ValueError):
            await self.api.read('alice', '/Company/scan.pdf')

    async def test_long_text_is_truncated_and_flagged(self) -> None:
        result = await self.api.read('alice', '/Company/large.txt')
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['text']), 30000)

    async def test_revoked_access_blocks_the_text(self) -> None:
        self.cloud.allowed = False
        with self.assertRaises(ValueError):
            await self.api.read('alice', '/Company/notes.md')

    async def test_browsing_lists_entries_without_returning_content(self) -> None:
        listing = await self.api.browse('alice', '/Company')
        self.assertEqual(
            {item['path'] for item in listing}, {'/Company/notes.md', '/Company/scan.pdf', '/Company/large.txt'}
        )
        self.assertNotIn('leave policy', json.dumps(listing))


class ActionProposals(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name), Fernet.generate_key().decode())
        for user in ('alice', 'bob'):
            self.store.connect(user, user, 'secret', 'http://cloud')

    def tearDown(self) -> None:
        self.directory.cleanup()

    def proposal(self) -> None:
        from nawantara_nextcloud.actions import listing

        payload = {
            'arguments': {'path': '/Documents/new'},
            'generation': self.store.connection('alice')['generation'],
        }
        with self.store.db() as db:
            db.execute(
                'INSERT INTO action_proposals VALUES (?,?,?,?,?,?,?,?)',
                (
                    'action',
                    'alice',
                    'create_folder',
                    self.store.cipher.encrypt(json.dumps(payload).encode()),
                    'pending',
                    self.store.cipher.encrypt(b'{}'),
                    time.time(),
                    time.time(),
                ),
            )
        self.assertEqual([item['operation'] for item in listing(self.store, 'alice')], ['create_folder'])

    def test_proposal_is_owner_only_and_can_be_claimed_once(self) -> None:
        from nawantara_nextcloud.actions import claim, listing

        self.proposal()
        self.assertEqual(listing(self.store, 'bob'), [])
        with self.assertRaises(ValueError):
            claim(self.store, 'bob', 'action', True)
        self.assertIsNotNone(claim(self.store, 'alice', 'action', True))
        with self.assertRaises(ValueError):
            claim(self.store, 'alice', 'action', True)

    def test_reconnected_account_cannot_execute_old_proposal(self) -> None:
        from nawantara_nextcloud.actions import claim

        self.proposal()
        self.store.connect('alice', 'other', 'other', 'http://cloud')
        with self.assertRaises(ValueError):
            claim(self.store, 'alice', 'action', True)

    def test_interrupted_write_is_uncertain_and_never_requeued(self) -> None:
        from nawantara_nextcloud.actions import claim, listing

        self.proposal()
        claim(self.store, 'alice', 'action', True)
        self.store.interrupt()
        self.assertEqual(listing(self.store, 'alice')[0]['status'], 'uncertain')
        with self.assertRaises(ValueError):
            claim(self.store, 'alice', 'action', True)


class DAVBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_conditional_creation_prevents_overwrite(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers['If-None-Match'], '*')
            self.assertEqual(request.url.path, '/remote.php/dav/files/alice/Company/new.txt')
            return httpx.Response(412)

        async with Cloud('https://cloud.example', 'alice', 'secret', httpx.MockTransport(respond)) as cloud:
            with self.assertRaises(CloudError):
                await cloud.create('/Company/new.txt', b'new')

    def test_path_validation_rejects_traversal_and_control_characters(self) -> None:
        self.assertEqual(path_name('Company/notes.md'), '/Company/notes.md')
        with self.assertRaises(ValueError):
            path_name('/Company/../Private/file')
        with self.assertRaises(ValueError):
            path_name('/Company/bad\nname')


if __name__ == '__main__':
    unittest.main()
