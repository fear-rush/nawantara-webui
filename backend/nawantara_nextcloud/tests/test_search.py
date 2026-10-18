"""Search modes remain explicit and caches never mix user corpora."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nawantara_nextcloud.search import SearchPool


def document(text, name='contract.txt'):
    return {
        'file_id': '1',
        'path': '/Documents/' + name,
        'etag': 'one',
        'digest': text,
        'chunks': [text],
        'vectors': [[1.0, 0.0]],
        'model': 'test',
    }


class SearchBehavior(unittest.TestCase):
    def setUp(self):
        self.pool = SearchPool()

    def tearDown(self):
        self.pool.close()

    def test_literal_does_not_silently_find_typo(self):
        docs = [document('Payment thirty days\nContract NW-2026-017')]
        self.assertEqual(self.pool.search('a', docs, 'Paymnt', 'literal', None), [])
        self.assertTrue(self.pool.search('a', docs, 'Paymnt', 'fuzzy', None))
        self.assertTrue(self.pool.search('a', docs, 'NW-[0-9]+', 'regex', None))
        self.assertTrue(self.pool.search('a', docs, 'cntract', 'filename', None))

    def test_other_users_never_search_private_snapshot(self):
        self.pool.search('alice', [document('private payroll')], 'payroll', 'literal', None)
        self.assertEqual(self.pool.search('bob', [document('public report')], 'payroll', 'literal', None), [])

    def test_changed_snapshot_and_invalidation_remove_plaintext(self):
        self.pool.search('a', [document('old content')], 'old', 'literal', None)
        directory = self.pool.snapshots['a'][1].root
        self.assertEqual(self.pool.search('a', [document('new content')], 'old', 'literal', None), [])
        self.assertFalse(directory.exists())
        directory = self.pool.snapshots['a'][1].root
        self.pool.invalidate('a')
        self.assertFalse(directory.exists())

    def test_hybrid_ranking_promotes_exact_identifier(self):
        docs = [document('unrelated'), {**document('Contract NW-2026-017'), 'file_id': '2'}]
        found = self.pool.search('a', docs, 'NW-2026-017', 'hybrid', [1.0, 0.0])
        self.assertEqual(found[0]['doc']['file_id'], '2')

    def test_invalid_regex_is_an_error_not_another_mode(self):
        with self.assertRaises(ValueError):
            self.pool.search('a', [document('hello')], '[', 'regex', None)
