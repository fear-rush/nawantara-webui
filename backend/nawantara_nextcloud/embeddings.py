"""Retrieval embedding contract. One provider, one vector space per index.

The index identity includes the provider contract, so switching provider or
model discards incompatible vectors instead of mixing them. See
docs/adr/0004-external-embedding-provider.md for what leaves the deployment.
"""

from __future__ import annotations

import asyncio
import math
import os
from collections.abc import Awaitable, Callable
from typing import Literal

import httpx

# Local EmbeddingGemma contract, served by the pinned llama.cpp container.
MODEL = 'embeddinggemma-2-Q8_0'
BASE_URL = 'http://embedding:8080/v1'
DIMENSIONS = 768
# Weight checksum, prompts and dimensions define one incompatible vector space.
INDEX_MODEL = MODEL + ':6f1bd4ac6c5df7444f9cca7ca36cafe6cfa34cd6f49fefb1e0b4be8143aed8bc:search-v1:768'
PREFIXES = {'document': 'title: none | text: ', 'query': 'task: search result | query: '}
LOCAL_BATCH = 32

# External provider contract. Voyage applies its own prompt through input_type
# instead of a text prefix, so the local prefixes must not be sent.
VOYAGE_BASE_URL = 'https://api.voyageai.com/v1'
VOYAGE_DEFAULT_MODEL = 'voyage-4'
VOYAGE_DEFAULT_DIMENSIONS = 1024
VOYAGE_DEFAULT_BATCH = 64
VOYAGE_ATTEMPTS = 8
VOYAGE_FIRST_DELAY = 5.0
VOYAGE_MAX_DELAY = 60.0
VOYAGE_MAX_INPUTS = 1000
# Bounded guard so a provider error can never echo indexed text back to an operator.
_DETAIL_PROBE = 40

Purpose = Literal['document', 'query']


def provider() -> str:
    value = os.environ.get('NEXTCLOUD_EMBEDDING_PROVIDER', '').strip().lower() or 'local'
    if value not in {'local', 'voyage'}:
        raise ValueError('NEXTCLOUD_EMBEDDING_PROVIDER must be local or voyage')
    return value


def voyage_model() -> str:
    return os.environ.get('VOYAGE_MODEL', '').strip() or VOYAGE_DEFAULT_MODEL


def _integer(name: str, default: int) -> int:
    raw = os.environ.get(name, '').strip()
    if not raw:
        return default
    if not raw.isdigit() or int(raw) <= 0:
        raise ValueError(f'{name} must be a positive integer')
    return int(raw)


def voyage_dimensions() -> int:
    return _integer('VOYAGE_DIMENSIONS', VOYAGE_DEFAULT_DIMENSIONS)


def batch_size() -> int:
    if provider() != 'voyage':
        return LOCAL_BATCH
    return _integer('VOYAGE_BATCH', VOYAGE_DEFAULT_BATCH)


def index_model() -> str:
    """Identity of the vector space currently in use."""
    if provider() == 'voyage':
        return f'voyage:{voyage_model()}:input-type-v1:{voyage_dimensions()}'
    return INDEX_MODEL


def validate(vectors: list, texts: list[str], dimensions: int, *, normalized: bool) -> list[list[float]]:
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError('Embedding service returned an unexpected number of vectors')
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != dimensions:
            raise ValueError('Embedding service returned an unexpected vector dimension')
        if any(not isinstance(value, (float, int)) or not math.isfinite(value) for value in vector):
            raise ValueError('Embedding service returned a non-finite vector')
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            raise ValueError('Embedding service returned an empty vector')
        if normalized and not math.isclose(norm, 1.0, abs_tol=0.01):
            raise ValueError('Embedding service returned an unnormalized vector')
    return vectors


async def encode(
    function: Callable[..., Awaitable[list[list[float]]]], texts: list[str], purpose: Purpose
) -> list[list[float]]:
    """Local provider. The pinned server normalizes and needs the task prefix."""
    vectors = await function([PREFIXES[purpose] + text for text in texts])
    return validate(vectors, texts, DIMENSIONS, normalized=True)


async def voyage_encode(
    texts: list[str], purpose: Purpose, *, transport: httpx.AsyncBaseTransport | None = None
) -> list[list[float]]:
    """External provider. input_type carries the query/document instruction."""
    key = os.environ.get('VOYAGE_API_KEY', '').strip()
    if not key:
        raise ValueError('VOYAGE_API_KEY is required for the voyage embedding provider')
    if len(texts) > VOYAGE_MAX_INPUTS:
        raise ValueError('Embedding batch exceeds the provider input limit')
    payload = {
        'input': texts,
        'model': voyage_model(),
        'input_type': 'document' if purpose == 'document' else 'query',
        'output_dtype': 'float',
        'truncation': True,
    }
    override = os.environ.get('VOYAGE_OUTPUT_DIMENSION', '').strip()
    if override:
        payload['output_dimension'] = _integer('VOYAGE_OUTPUT_DIMENSION', voyage_dimensions())
    async with httpx.AsyncClient(base_url=VOYAGE_BASE_URL, timeout=120, transport=transport) as client:
        response = await _post(client, payload, key, texts)
    body = response.json()
    data = body.get('data') if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise ValueError('Embedding service returned an unexpected response')
    ordered = sorted(data, key=lambda item: item.get('index', 0) if isinstance(item, dict) else 0)
    vectors = [item.get('embedding') if isinstance(item, dict) else None for item in ordered]
    return validate(vectors, texts, voyage_dimensions(), normalized=False)


async def _post(
    client: httpx.AsyncClient, payload: dict, key: str, texts: list[str]
) -> httpx.Response:
    """Patient retry. Tier-limited accounts allow only a few requests per minute."""
    delay = _first_delay()
    for attempt in range(1, VOYAGE_ATTEMPTS + 1):
        response = await client.post('/embeddings', json=payload, headers={'Authorization': 'Bearer ' + key})
        if response.status_code < 400:
            return response
        retryable = response.status_code in {429, 500, 502, 503, 504}
        if not retryable or attempt == VOYAGE_ATTEMPTS:
            raise ValueError(
                f'Embedding provider rejected the request ({response.status_code})'
                + _detail(response, texts)
            )
        retry_after = response.headers.get('retry-after', '')
        await asyncio.sleep(min(float(retry_after), VOYAGE_MAX_DELAY) if _is_number(retry_after) else delay)
        delay = min(delay * 2, VOYAGE_MAX_DELAY)
    raise ValueError('Embedding provider unavailable')


def _first_delay() -> float:
    raw = os.environ.get('VOYAGE_RETRY_DELAY', '').strip()
    if not raw:
        return VOYAGE_FIRST_DELAY
    try:
        return max(float(raw), 0.0)
    except ValueError:
        return VOYAGE_FIRST_DELAY


def _is_number(value: str) -> bool:
    return bool(value) and value.replace('.', '', 1).isdigit()


def _detail(response: httpx.Response, texts: list[str]) -> str:
    """Explain a rejection without risking document text.

    Rate-limit failures are reported in full, because they are actionable. The
    detail is withheld whenever it restates part of an input, so a provider that
    echoes content cannot turn an error message into an exfiltration path.
    """
    if response.status_code != 429:
        return ''
    try:
        detail = response.json().get('detail', '')
    except (ValueError, AttributeError):
        return ''
    if not isinstance(detail, str) or not detail:
        return ''
    for text in texts:
        probe = text.strip()[:_DETAIL_PROBE]
        if probe and probe in detail:
            return ''
    return ': ' + detail[:200]
