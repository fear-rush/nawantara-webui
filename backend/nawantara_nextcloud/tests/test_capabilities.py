"""Change notifications, approvals and generated files preserve user boundaries."""

import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nawantara_nextcloud.actions import claim, listing
from nawantara_nextcloud.analysis import file_bytes
from nawantara_nextcloud.events import dispatch, notify
from nawantara_nextcloud.store import Store


class CapabilityBehavior(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name), Fernet.generate_key().decode())
        for user in ('alice', 'bob'):
            self.store.connect(user, user, 'private', ['/Documents'], 'http://cloud')

    def tearDown(self):
        self.store.search.close()
        self.directory.cleanup()

    def event(self):
        return {'event': {'class': 'OCP\\Files\\Events\\Node\\NodeWrittenEvent', 'node': {'path': '/private'}}}

    def proposal(self):
        payload = {'arguments': {'path': '/Documents/new'}, 'generation': self.store.connection('alice')['generation']}
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

    def test_event_burst_coalesces_without_dropping_change_during_active_job(self):
        notify(self.store, self.event())
        notify(self.store, self.event())
        dispatch(self.store)
        self.assertEqual(len(self.store.jobs('alice')), 1)
        job = self.store.claim()
        notify(self.store, self.event())
        dispatch(self.store)
        self.assertEqual(len(self.store.jobs(job['user_id'])), 1)
        self.store.update(job['id'], 'completed', 'completed')
        dispatch(self.store)
        self.assertEqual(len(self.store.jobs(job['user_id'])), 2)

    def test_event_payload_does_not_grant_content_access(self):
        notify(self.store, self.event())
        self.assertEqual(self.store.documents('bob'), [])
        with self.assertRaises(ValueError):
            notify(self.store, {'event': {'class': 'arbitrary'}})

    def test_action_is_owner_only_and_can_be_claimed_once(self):
        self.proposal()
        self.assertEqual(listing(self.store, 'bob'), [])
        with self.assertRaises(ValueError):
            claim(self.store, 'bob', 'action', True)
        self.assertIsNotNone(claim(self.store, 'alice', 'action', True))
        with self.assertRaises(ValueError):
            claim(self.store, 'alice', 'action', True)

    def test_reconnected_account_cannot_execute_old_proposal(self):
        self.proposal()
        self.store.connect('alice', 'other', 'other', ['/Documents'], 'http://cloud')
        with self.assertRaises(ValueError):
            claim(self.store, 'alice', 'action', True)

    def test_interrupted_write_is_uncertain_and_never_requeued(self):
        self.proposal()
        claim(self.store, 'alice', 'action', True)
        self.store.interrupt()
        self.assertEqual(listing(self.store, 'alice')[0]['status'], 'uncertain')
        with self.assertRaises(ValueError):
            claim(self.store, 'alice', 'action', True)

    def test_generated_files_are_owned_and_removed_on_disconnect(self):
        result = {'files': [{'name': 'report.csv', 'data': base64.b64encode(b'private').decode()}]}
        with self.store.db() as db:
            db.execute(
                'INSERT INTO analysis_runs VALUES (?,?,?,?)',
                (
                    'run',
                    'alice',
                    self.store.cipher.encrypt(json.dumps(result).encode()),
                    time.time(),
                ),
            )
        with self.assertRaises(ValueError):
            file_bytes(self.store, 'bob', 'run', 'report.csv')
        self.assertEqual(file_bytes(self.store, 'alice', 'run', 'report.csv'), b'private')
        self.store.disconnect('alice')
        with self.assertRaises(ValueError):
            file_bytes(self.store, 'alice', 'run', 'report.csv')


class VersionListingBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_current_mtime_is_not_offered_as_a_retained_download(self):
        import httpx
        from nawantara_nextcloud.client import Cloud

        def respond(request):
            return httpx.Response(
                207,
                content=b"""<d:multistatus xmlns:d="DAV:">
              <d:response><d:href>/remote.php/dav/versions/alice/versions/42/</d:href></d:response>
              <d:response><d:href>/remote.php/dav/versions/alice/versions/42/9</d:href></d:response>
              <d:response><d:href>/remote.php/dav/versions/alice/versions/42/10</d:href></d:response>
            </d:multistatus>""",
            )

        async with Cloud('http://cloud', 'alice', 'private', transport=httpx.MockTransport(respond)) as cloud:
            self.assertEqual(await cloud.versions('42', 10), [{'version': '9', 'timestamp': 9}])
