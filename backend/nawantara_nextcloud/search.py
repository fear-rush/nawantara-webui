"""User-isolated search snapshots. Explicit modes never substitute for each other."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

from fff import FFFException, FileFinder

MODES = {'hybrid', 'semantic', 'keyword', 'literal', 'regex', 'fuzzy', 'filename'}


def cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError('Incompatible search vector dimensions')
    norm = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / norm if norm else 0.0


class Snapshot:
    def __init__(self, documents: list[dict]) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix='nawantara-search-')
        self.root = Path(self.directory.name)
        self.corpus = self.root / 'corpus'
        self.corpus.mkdir(mode=0o700)
        self.rows = []
        self.paths = {}
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.execute('CREATE VIRTUAL TABLE passages USING fts5(path, text)')
        self.finder = None
        try:
            for doc in documents:
                for number, (text, vector) in enumerate(zip(doc['chunks'], doc['vectors'], strict=True)):
                    key = len(self.rows)
                    identity = hashlib.sha256(doc['file_id'].encode()).hexdigest()
                    relative = f'{identity}/{Path(doc["path"]).name}/{number + 1}.txt'
                    target = self.corpus / relative
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    target.write_text(text, encoding='utf-8')
                    target.chmod(0o600)
                    self.paths[relative] = key
                    self.rows.append({'doc': doc, 'chunk': number + 1, 'text': text, 'vector': vector})
                    self.db.execute(
                        'INSERT INTO passages(rowid,path,text) VALUES (?,?,?)', (key + 1, doc['path'], text)
                    )
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if self.finder is not None:
            self.finder.close()
        self.db.close()
        self.directory.cleanup()

    def keyword(self, query: str) -> list[int]:
        terms = re.findall(r'\w+', query, flags=re.UNICODE)
        if not terms:
            raise ValueError('Keyword search needs at least one word or number')
        expression = ' OR '.join('"' + term + '"' for term in terms)
        return [
            row[0] - 1
            for row in self.db.execute(
                'SELECT rowid FROM passages WHERE passages MATCH ? ORDER BY bm25(passages,2,1) LIMIT 100',
                (expression,),
            )
        ]

    def file_search(self, query: str, mode: str) -> list[int]:
        if self.finder is None:
            self.finder = FileFinder(
                str(self.corpus),
                watch=False,
                ai_mode=False,
                follow_symlinks=False,
                frecency_db_path=str(self.root / 'frequency'),
                history_db_path=str(self.root / 'history'),
            )
            if not self.finder.wait_for_scan_blocking(timeout_ms=30000):
                self.finder.close()
                self.finder = None
                raise ValueError('File search initialization timed out')
        if mode == 'filename':
            result = self.finder.search(query, page_size=100)
        else:
            result = self.finder.grep(
                query,
                mode='plain' if mode == 'literal' else mode,
                page_limit=100,
                max_matches_per_file=1,
                smart_case=False,
            )
        if mode == 'regex' and result.regex_fallback_error:
            raise ValueError('Invalid regular expression')
        return list(dict.fromkeys(self.paths[item.relative_path] for item in result.items))

    def rank(self, query: str, mode: str, vector: list[float] | None) -> list[dict]:
        lists = []
        if mode in {'hybrid', 'semantic'}:
            if vector is None:
                raise ValueError('Semantic search requires an embedding')
            lists.append(
                sorted(range(len(self.rows)), key=lambda k: cosine(vector, self.rows[k]['vector']), reverse=True)[:100]
            )
        if mode in {'hybrid', 'keyword'}:
            lists.append(self.keyword(query))
        if mode in {'literal', 'regex', 'fuzzy', 'filename'}:
            lists.append(self.file_search(query, mode))
        scores = {}
        for ranking in lists:
            for position, key in enumerate(ranking):
                scores[key] = scores.get(key, 0) + 1 / (60 + position + 1)
        return [{**self.rows[key], 'score': scores[key]} for key in sorted(scores, key=scores.get, reverse=True)]


class SearchPool:
    """Bounded process-local snapshots. Source checks precede every query."""

    def __init__(self) -> None:
        self.snapshots = OrderedDict()
        self.lock = threading.RLock()

    def invalidate(self, user_id: str) -> None:
        with self.lock:
            entry = self.snapshots.pop(user_id, None)
            if entry:
                entry[1].close()

    def close(self) -> None:
        with self.lock:
            for user_id in list(self.snapshots):
                self.invalidate(user_id)

    def search(
        self, user_id: str, documents: list[dict], query: str, mode: str, vector: list[float] | None
    ) -> list[dict]:
        if not documents:
            self.invalidate(user_id)
            return []
        signature = hashlib.sha256(json.dumps(documents, sort_keys=True).encode()).hexdigest()
        with self.lock:
            previous = self.snapshots.get(user_id)
            if previous is None or previous[0] != signature:
                self.invalidate(user_id)
                while len(self.snapshots) >= 4:
                    self.invalidate(next(iter(self.snapshots)))
                self.snapshots[user_id] = (signature, Snapshot(documents))
            self.snapshots.move_to_end(user_id)
            try:
                return self.snapshots[user_id][1].rank(query, mode, vector)
            except FFFException:
                raise ValueError('File search failed. Check the query and search mode.') from None
