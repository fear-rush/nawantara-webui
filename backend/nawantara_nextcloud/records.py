"""Group files that carry one document and choose the single text to index.

External OCR pipelines emit several files per publication. A PDF, its Markdown
transcription and its structure JSON are one document, not three. Only the
Markdown is indexed as text: a multi-megabyte structure JSON would become
thousands of punctuation-only passages. See docs/document-categorization-and-flow.md.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

ORIGINAL = 'original'
TEXT = 'text'
STRUCTURE = 'structure'
FILE = 'file'

ORIGINAL_SUFFIXES = {'.pdf'}
TEXT_SUFFIXES = {'.md', '.txt', '.csv'}
STRUCTURE_SUFFIXES = {'.json'}

# Producer affixes applied by the OCR pipeline in front of the source name.
_PREFIXES = ('mineru_markdown_', 'mineru_')
_PADDLEOCR = '_by_paddleocr'
_TRAILING_ID = re.compile(r'[_-]{1,2}\d{8,}$')
_NOT_ALPHANUMERIC = re.compile(r'[^a-z0-9]+')

Entry = tuple[str, str]


def classify(name: str) -> str:
    suffix = PurePosixPath(name).suffix.lower()
    if suffix in ORIGINAL_SUFFIXES:
        return ORIGINAL
    if suffix in STRUCTURE_SUFFIXES:
        return STRUCTURE
    if suffix in TEXT_SUFFIXES:
        return TEXT
    return FILE


def normalize(value: str) -> str:
    return _NOT_ALPHANUMERIC.sub('-', value.lower()).strip('-')


def stem(name: str) -> str:
    """Reduce a file name to the source name it was derived from."""
    value = name.lower()
    for prefix in _PREFIXES:
        if value.startswith(prefix):
            value = value[len(prefix) :]
            break
    paddle = value.find(_PADDLEOCR)
    if paddle >= 0:
        value = value[:paddle]
    for _ in range(2):
        suffix = PurePosixPath(value).suffix.lower()
        if suffix in STRUCTURE_SUFFIXES | TEXT_SUFFIXES | ORIGINAL_SUFFIXES:
            value = value[: -len(suffix)]
        else:
            break
    return normalize(_TRAILING_ID.sub('', value))


@dataclass
class Group:
    """One document: an optional original, one indexed text and its structure."""

    record_id: str
    title: str
    original_file_id: str
    original_path: str
    text_file_id: str = ''
    text_path: str = ''
    structure_file_id: str = ''
    structure_path: str = ''
    representations: list[dict] = field(default_factory=list)


def title_of(path: str) -> str:
    name = PurePosixPath(path).name
    suffix = PurePosixPath(name).suffix
    return name[: -len(suffix)] if suffix else name


def member(file_id: str, path: str) -> dict:
    return {'file_id': file_id, 'path': path, 'kind': classify(path)}


def fingerprint(members: list[dict]) -> str:
    """Change marker over every file that carries the document."""
    joined = '|'.join(sorted(f'{item["file_id"]}:{item.get("etag", "")}' for item in members))
    return hashlib.sha256(joined.encode()).hexdigest()


def group(members: list[Entry]) -> list[Group]:
    """Group (file_id, path) pairs into documents.

    Pairing is by normalized name inside one folder, which is how the OCR
    pipeline names its output. A folder holding a single PDF claims every
    unmatched Markdown or JSON beside it.
    """
    folders: dict[str, list[Entry]] = {}
    for file_id, path in members:
        folders.setdefault(str(PurePosixPath(path).parent), []).append((file_id, path))

    groups: list[Group] = []
    for files in sorted(folders.values()):
        groups.extend(_folder(files))
    return groups


def _folder(files: list[Entry]) -> list[Group]:
    originals = [entry for entry in files if classify(entry[1]) == ORIGINAL]
    derived = [entry for entry in files if classify(entry[1]) in {TEXT, STRUCTURE}]
    groups = []
    claimed: set[str] = set()
    for file_id, path in originals:
        item = Group(record_id=file_id, title=title_of(path), original_file_id=file_id, original_path=path)
        item.representations.append(member(file_id, path))
        wanted = stem(PurePosixPath(path).name)
        for candidate_id, candidate_path in derived:
            if candidate_id not in claimed and stem(PurePosixPath(candidate_path).name) == wanted:
                claimed.add(candidate_id)
                _attach(item, candidate_id, candidate_path)
        groups.append(item)

    # A lone PDF in a folder owns whatever Markdown and JSON sit beside it.
    leftovers = [entry for entry in derived if entry[0] not in claimed]
    if len(originals) == 1:
        for file_id, path in leftovers:
            _attach(groups[-1], file_id, path)
        leftovers = []

    # A Markdown without a PDF is its own text. A structure JSON is not indexed.
    groups.extend(_standalone(file_id, path) for file_id, path in leftovers if classify(path) == TEXT)
    groups.extend(_standalone(file_id, path) for file_id, path in files if classify(path) == FILE)
    return groups


def _standalone(file_id: str, path: str) -> Group:
    text = classify(path) == TEXT
    return Group(
        record_id=file_id,
        title=title_of(path),
        original_file_id=file_id,
        original_path=path,
        text_file_id=file_id if text else '',
        text_path=path if text else '',
        representations=[member(file_id, path)],
    )


def _attach(item: Group, file_id: str, path: str) -> None:
    kind = classify(path)
    item.representations.append(member(file_id, path))
    if kind == TEXT and not item.text_file_id:
        item.text_file_id, item.text_path = file_id, path
    elif kind == STRUCTURE and not item.structure_file_id:
        item.structure_file_id, item.structure_path = file_id, path
