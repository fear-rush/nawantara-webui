"""Protect the embedding contract from incorrect prompts and unusable vectors."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from nawantara_nextcloud import embeddings
from nawantara_nextcloud.embeddings import DIMENSIONS, INDEX_MODEL, encode, index_model, voyage_encode
from nawantara_nextcloud.store import Store

VOYAGE_VECTOR = [0.5, 0.5] + [0.0] * (embeddings.VOYAGE_DEFAULT_DIMENSIONS - 2)


class VoyageProvider(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.saved = dict(embeddings.os.environ)
        embeddings.os.environ['VOYAGE_API_KEY'] = 'test-key'

    def tearDown(self) -> None:
        embeddings.os.environ.clear()
        embeddings.os.environ.update(self.saved)

    async def test_document_and_query_use_input_type_instead_of_prefixes(self) -> None:
        seen = []

        def respond(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            seen.append(body)
            self.assertEqual(request.headers['authorization'], 'Bearer test-key')
            self.assertEqual(request.url.path, '/v1/embeddings')
            return httpx.Response(
                200,
                json={'data': [{'index': index, 'embedding': VOYAGE_VECTOR} for index in range(len(body['input']))]},
            )

        transport = httpx.MockTransport(respond)
        await voyage_encode(['query text'], 'query', transport=transport)
        await voyage_encode(['passage text'], 'document', transport=transport)
        self.assertEqual([body['input_type'] for body in seen], ['query', 'document'])
        self.assertEqual([body['input'] for body in seen], [['query text'], ['passage text']])
        self.assertNotIn('title: none | text: ', json.dumps(seen))

    async def test_unordered_response_is_reassembled_by_index(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    'data': [
                        {'index': 1, 'embedding': [0.0, 1.0] + [0.0] * (embeddings.VOYAGE_DEFAULT_DIMENSIONS - 2)},
                        {'index': 0, 'embedding': VOYAGE_VECTOR},
                    ]
                },
            )

        vectors = await voyage_encode(['first', 'second'], 'document', transport=httpx.MockTransport(respond))
        self.assertEqual(vectors[0], VOYAGE_VECTOR)
        self.assertEqual(vectors[1][1], 1.0)

    async def test_rate_limit_is_retried_then_reported_without_body_text(self) -> None:
        calls = []

        def respond(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return httpx.Response(429, text='secret passage text from the document')

        with self.assertRaises(ValueError) as caught:
            await voyage_encode(['passage'], 'document', transport=httpx.MockTransport(respond))
        self.assertEqual(len(calls), embeddings.VOYAGE_ATTEMPTS)
        self.assertNotIn('secret passage text', str(caught.exception))

    async def test_missing_key_and_wrong_dimension_fail_closed(self) -> None:
        embeddings.os.environ['VOYAGE_API_KEY'] = ''
        with self.assertRaises(ValueError):
            await voyage_encode(['passage'], 'document')
        embeddings.os.environ['VOYAGE_API_KEY'] = 'test-key'

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [1.0, 0.0]}]})

        with self.assertRaises(ValueError):
            await voyage_encode(['passage'], 'document', transport=httpx.MockTransport(respond))

    def test_provider_switch_changes_the_index_identity_and_batch(self) -> None:
        embeddings.os.environ['NEXTCLOUD_EMBEDDING_PROVIDER'] = 'local'
        local = index_model()
        self.assertEqual(local, INDEX_MODEL)
        self.assertEqual(embeddings.batch_size(), embeddings.LOCAL_BATCH)
        embeddings.os.environ['NEXTCLOUD_EMBEDDING_PROVIDER'] = 'voyage'
        self.assertEqual(index_model(), 'voyage:voyage-4:input-type-v1:1024')
        self.assertEqual(embeddings.batch_size(), embeddings.VOYAGE_BATCH)
        self.assertNotEqual(index_model(), local)
        embeddings.os.environ['NEXTCLOUD_EMBEDDING_PROVIDER'] = 'nonsense'
        with self.assertRaises(ValueError):
            index_model()


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
