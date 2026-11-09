"""Exact cache keys and the immutable corpus used to render an approved answer."""

import csv
import hashlib
import json
import unicodedata
from dataclasses import asdict, dataclass
from io import StringIO
from pathlib import Path

from lawbot import answer_format, citation
from lawbot.corpus_store import CorpusRelease
from lawbot.hits import Hit

NORMALIZER = 'normalizer_v1'
MODEL = 'pro'
FORMAT = 'concise'
PROFILE = 'v5'
DISPLAY_VERSION = 'exact-display-v1'


def normalize(question: str) -> str:
    return unicodedata.normalize('NFC', question).replace('\r\n', '\n').strip()


def digest(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


@dataclass(frozen=True)
class Namespace:
    corpus_version: str
    answer_policy_version: str
    domain_scope: str
    normalizer_version: str = NORMALIZER
    visibility_scope: str = 'public'
    locale: str = 'ko-KR'

    def serialize(self) -> str:
        return canonical(asdict(self))

    @property
    def id(self) -> str:
        return digest(self.serialize())


@dataclass(frozen=True)
class Article:
    law: str
    number: str
    title: str
    kind: str
    text: str
    text_hash: str

    def hit(self) -> Hit:
        return Hit(
            law=self.law,
            article_id=self.number,
            title=self.title,
            kind=self.kind,
            doc=f'{self.law} {self.number}({self.title})\n{self.text}',
            score=0.0,
        )


def read_articles(raw: bytes) -> dict[str, Article]:
    """Keep the raw CSV text and omit deleted or ambiguous article IDs."""
    articles = {}
    seen = set()
    for row in csv.DictReader(StringIO(raw.decode('utf-8-sig'), newline='')):
        key = row['article_id']
        if key in seen:
            articles.pop(key, None)
            continue
        seen.add(key)
        if row['deleted'] == '0':
            text = row['text']
            articles[key] = Article(
                row['law'], row['article_no'], row['title'], row.get('kind', '법률'), text, digest(text)
            )
    return articles


@dataclass(frozen=True)
class Snapshot:
    release_id: str
    namespace: Namespace
    articles: dict[str, Article]

    @classmethod
    def load(cls, release: CorpusRelease, *, system: str, combined: bool, attach_decrees: bool):
        law_hash = release.law.manifest['articles_sha256']
        combined_hash = release.combined.manifest['articles_sha256']
        corpus = digest(canonical([PROFILE, release.release_id, law_hash, combined_hash]))
        renderer_hashes = [
            hashlib.sha256(Path(path).read_bytes()).hexdigest()
            for path in (answer_format.__file__, citation.__file__)
            if path is not None
        ]
        policy = digest(canonical([MODEL, FORMAT, digest(system), DISPLAY_VERSION, renderer_hashes]))
        scope = canonical([PROFILE, 'combined' if combined else 'law', attach_decrees])
        part = release.combined if combined else release.law
        articles = read_articles(part.data)
        if not attach_decrees:
            articles = {key: value for key, value in articles.items() if value.kind == '법률'}
        return cls(release.release_id, Namespace(corpus, policy, scope), articles)

    def resolve(self, citations: list[tuple[str, str]]) -> list[Article] | None:
        if not citations or len({key for key, _ in citations}) != len(citations):
            return None
        articles = []
        for key, text_hash in citations:
            article = self.articles.get(key)
            if article is None or article.text_hash != text_hash:
                return None
            articles.append(article)
        return articles

    def render(self, raw: str, citations: list[tuple[str, str]]) -> str | None:
        articles = self.resolve(citations)
        if articles is None:
            return None
        hits = [article.hit() for article in articles]
        used, unknown, refused = answer_format.answer_citations(raw, hits)
        if unknown or refused or used != hits:
            return None
        return answer_format.render(raw, hits, FORMAT, done=True)
