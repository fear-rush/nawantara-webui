"""Fixed EmbeddingGemma retrieval contract and response validation."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from typing import Literal

MODEL = 'embeddinggemma-2-Q8_0'
BASE_URL = 'http://embedding:8080/v1'
DIMENSIONS = 768
# Weight checksum, prompts and dimensions define one incompatible vector space.
INDEX_MODEL = MODEL + ':6f1bd4ac6c5df7444f9cca7ca36cafe6cfa34cd6f49fefb1e0b4be8143aed8bc:search-v1:768'
Purpose = Literal['document', 'query']
PREFIXES = {'document': 'title: none | text: ', 'query': 'task: search result | query: '}


async def encode(
    function: Callable[..., Awaitable[list[list[float]]]], texts: list[str], purpose: Purpose
) -> list[list[float]]:
    vectors = await function([PREFIXES[purpose] + text for text in texts])
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError('Embedding service returned an unexpected number of vectors')
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != DIMENSIONS:
            raise ValueError('Embedding service returned an unexpected vector dimension')
        if any(not isinstance(value, (float, int)) or not math.isfinite(value) for value in vector):
            raise ValueError('Embedding service returned a non-finite vector')
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isclose(norm, 1.0, abs_tol=0.01):
            raise ValueError('Embedding service returned an unnormalized vector')
    return vectors
