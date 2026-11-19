"""Build cross-law definition links from the active statutory corpus."""

import re
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

MAX_TERM_SHARE = 0.05
MAX_REFERENCE_HOPS = 3
_ITEM = re.compile(r'^\s*(\d+)\.\s*"([^"]+)"(?:이)?란\s*(.+)$')
_PARAGRAPH = re.compile(r'^[^\S\n]*[①-⑳㉑-㉟㊱-㊿]', re.M)
_PARAGRAPH_REF = re.compile(r'제1항제(\d+)호')
_EXTERNAL_REF = re.compile(r'「([^」]+)」\s*(제\d+조(?:의\d+)?)제1항제(\d+)호에\s*따른')

type ItemKey = tuple[str, str, str]
type ArticleKey = tuple[str, str]


@dataclass(frozen=True, slots=True)
class Article:
    law: str
    article_id: str
    title: str
    text: str


@dataclass(frozen=True, slots=True)
class DefinitionItem:
    law: str
    article_id: str
    title: str
    number: str
    term: str
    text: str


def _parse(article: Article) -> Iterator[tuple[DefinitionItem, ItemKey | None]]:
    items = [match for line in article.text.splitlines() if (match := _ITEM.match(line))]
    if not items:
        return
    related: dict[str, list[str]] = defaultdict(list)
    markers = list(_PARAGRAPH.finditer(article.text))
    for i, marker in enumerate(markers):
        if marker[0].endswith('①'):
            continue
        end = markers[i + 1].start() if i + 1 < len(markers) else len(article.text)
        paragraph = article.text[marker.start() : end].strip()
        for number in dict.fromkeys(_PARAGRAPH_REF.findall(paragraph)):
            related[number].append(paragraph)
    for match in items:
        number, term, body = match.groups()
        text = '\n'.join([match[0].strip(), *related.get(number, ())])
        ref = _EXTERNAL_REF.search(body)
        target = (ref[1], ref[2], ref[3]) if ref and ref[1] != article.law else None
        yield DefinitionItem(article.law, article.article_id, article.title, number, term, text), target


def _terminal(
    target: ItemKey, definitions: dict[ItemKey, tuple[DefinitionItem, ItemKey | None]]
) -> DefinitionItem | None:
    for _ in range(MAX_REFERENCE_HOPS):
        entry = definitions.get(target)
        if entry is None:
            break
        item, ref = entry
        if ref is None:
            return item
        target = ref
    return None


class DefinitionLinks:
    """Index terminal cross-law definitions by the article that uses the defined term."""

    def __init__(self, articles: Iterable[Article]):
        corpus = tuple(articles)
        definitions = {
            (item.law, item.article_id, item.number): (item, target)
            for article in corpus
            for item, target in _parse(article)
        }
        terms = {item.term for item, _ in definitions.values()}
        limit = len(corpus) * MAX_TERM_SHARE
        frequent = {term for term in terms if sum(term in article.text for article in corpus) > limit}
        self.n_definitions = len(definitions)
        self.n_frequent_terms = len(frequent)

        by_law: dict[str, list[tuple[DefinitionItem, DefinitionItem]]] = defaultdict(list)
        for item, ref in definitions.values():
            if ref is not None and item.term not in frequent:
                target = _terminal(ref, definitions)
                if target is not None and target.law != item.law:
                    by_law[item.law].append((item, target))

        self._links: dict[ArticleKey, tuple[DefinitionItem, ...]] = {}
        for article in corpus:
            if matches := tuple(
                target
                for item, target in by_law.get(article.law, ())
                if article.article_id != item.article_id and item.term in article.text
            ):
                self._links[article.law, article.article_id] = matches

    def for_article(self, law: str, article_id: str) -> tuple[DefinitionItem, ...]:
        return self._links.get((law, article_id), ())
