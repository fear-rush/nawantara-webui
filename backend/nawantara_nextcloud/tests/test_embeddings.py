"""Protect the embedding contract from incorrect prompts and unusable vectors."""

import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nawantara_nextcloud.embeddings import DIMENSIONS, INDEX_MODEL, encode
from nawantara_nextcloud.store import Store


class EmbeddingBehavior(unittest.IsolatedAsyncioTestCase):
    async def test_queries_and_documents_use_distinct_prompts_once(self):
        seen = []

        async def inference(texts):
            seen.extend(texts)
            return [[1.0] + [0.0] * (DIMENSIONS - 1) for _ in texts]

        await encode(inference, ['leave policy'], 'query')
        await encode(inference, ['Employees receive annual leave.'], 'document')
        self.assertEqual(
            seen,
            [
                'task: search result | query: leave policy',
                'title: none | text: Employees receive annual leave.',
            ],
        )

    async def test_invalid_vectors_fail_instead_of_entering_index(self):
        invalid = [
            [],
            [[1.0, 0.0]],
            [[float('nan')] * DIMENSIONS],
            [[float('inf')] * DIMENSIONS],
            [[0.0] * DIMENSIONS],
            [[1.0] * DIMENSIONS],
        ]
        for result in invalid:

            async def inference(texts):
                return result

            with self.subTest(result_type=len(result)), self.assertRaises(ValueError):
                await encode(inference, ['document'], 'document')

    async def test_unavailable_service_does_not_use_another_model(self):
        async def inference(texts):
            raise ConnectionError('service unavailable')

        with self.assertRaises(ConnectionError):
            await encode(inference, ['document'], 'document')

    def test_replacement_removes_only_incompatible_derived_data(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory), Fernet.generate_key().decode())
            store.connect('alice', 'alice', 'app-password', ['/Documents'], 'http://cloud')
            account = store.connection('alice')
            for file_id, model in [('old', 'previous-embedding'), ('new', INDEX_MODEL)]:
                store.put_document(
                    'alice',
                    {
                        'file_id': file_id,
                        'path': '/Documents/' + file_id,
                        'etag': 'revision',
                        'digest': 'hash',
                        'chunks': ['text'],
                        'vectors': [[1.0]],
                        'model': model,
                    },
                    account['generation'],
                )
            store.synced('alice')
            store.discard_other_embeddings(INDEX_MODEL)
            self.assertEqual([d['file_id'] for d in store.documents('alice')], ['new'])
            self.assertEqual(store.connection('alice')['password'], 'app-password')
            self.assertIn('alice', store.due_accounts())
