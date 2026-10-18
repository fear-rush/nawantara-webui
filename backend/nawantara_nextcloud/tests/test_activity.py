"""Activity filters and event history must preserve account and content boundaries."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nawantara_nextcloud import activity
from nawantara_nextcloud.store import Store


class ActivityBehavior(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name), Fernet.generate_key().decode())
        for user in ['alice', 'bob']:
            self.store.connect(user, user, 'private-secret', ['/Private'], 'http://cloud')

    def tearDown(self):
        self.directory.cleanup()

    def test_admin_history_contains_only_explicit_safe_counters(self):
        job = self.store.enqueue('alice', 'save', {'text': 'confidential document', 'path': '/private-file.md'})
        self.store.claim()
        self.store.update(
            job,
            'completed',
            'completed',
            {
                'url': 'https://cloud/private-file',
                'indexed': 3,
                'filename': 'private-file',
                'text': 'confidential document',
                'processed': 'private-secret',
            },
        )
        result = activity.detail(self.store, job)
        serialized = json.dumps(result)
        for forbidden in ['confidential', 'private-file', 'private-secret']:
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(result['job']['progress'], {'indexed': 3})
        self.assertEqual([e['stage'] for e in result['events']], ['queued', 'starting', 'completed'])
        self.assertEqual(result['job']['result'], {})
        self.assertIn('url', activity.detail(self.store, job, 'alice')['job']['result'])

    def test_owner_cannot_read_another_users_job(self):
        job = self.store.enqueue('alice', 'index', {})
        with self.assertRaises(KeyError):
            activity.detail(self.store, job, 'bob')

    def test_filter_counts_and_pagination_cover_more_than_one_page(self):
        for index in range(25):
            job = self.store.enqueue('alice', 'save', {})
            self.store.update(job, 'failed' if index < 5 else 'completed', 'completed')
        self.store.enqueue('bob', 'save', {})
        first = activity.listing(self.store, actor='alice')
        second = activity.listing(self.store, actor='alice', page=2)
        self.assertEqual(first['total'], 25)
        self.assertEqual(len(first['jobs']), 20)
        self.assertEqual(len(second['jobs']), 5)
        self.assertFalse({j['id'] for j in first['jobs']} & {j['id'] for j in second['jobs']})
        filtered = activity.listing(self.store, actor='alice', status='attention')
        self.assertEqual(filtered['total'], 5)
        self.assertEqual(filtered['summary']['completed'], 20)
        self.assertEqual(activity.listing(self.store, actor='alice', action='index')['total'], 0)
        self.assertEqual(activity.listing(self.store, since=10**12)['total'], 0)
        self.assertEqual(activity.listing(self.store, query=first['jobs'][0]['id'])['total'], 1)

    def test_retry_and_restart_keep_persisted_history(self):
        job = self.store.enqueue('alice', 'index', {})
        self.store.claim()
        self.store.interrupt()
        self.store.control(job, 'alice', 'retry')
        self.store.claim()
        self.store.update(job, 'completed', 'completed')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM job_events WHERE job_id=?', (job,)).fetchone()[0], 6)
        self.assertEqual(activity.detail(self.store, job)['events'][2]['status'], 'interrupted')

    def test_cancel_requires_owner_and_valid_current_status(self):
        job = self.store.enqueue('alice', 'save', {})
        with self.assertRaises(ValueError):
            self.store.control(job, 'bob', 'cancel')
        self.assertEqual(activity.detail(self.store, job)['events_total'], 1)
        self.store.control(job, 'alice', 'cancel')
        self.assertEqual(activity.detail(self.store, job)['job']['status'], 'cancelled')
        with self.assertRaises(ValueError):
            self.store.control(job, 'alice', 'cancel')

    def test_disconnect_removes_history_and_late_updates_cannot_recreate_it(self):
        job = self.store.enqueue('alice', 'save', {})
        self.store.disconnect('alice')
        self.store.update(job, 'completed', 'completed')
        with self.store.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM job_events').fetchone()[0], 0)
