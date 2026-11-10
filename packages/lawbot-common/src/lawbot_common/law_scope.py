"""Load the ordered law scope shared by the corpus, monitor and calendar."""

import unicodedata
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

SCOPE_FILE = Path(__file__).with_name('law_scope.json')


class LawScope(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra='forbid', str_strip_whitespace=True)

    name: str = Field(min_length=1)
    folder: str = Field(pattern=r'^[^\s/\\]+$')
    category: int = Field(ge=1, le=7)
    keywords: tuple[Annotated[str, StringConstraints(min_length=1)], ...] = Field(min_length=1)
    decrees: tuple[Literal['시행령', '시행규칙'], ...]


def load_law_scope() -> tuple[LawScope, ...]:
    scope = TypeAdapter(tuple[LawScope, ...]).validate_json(SCOPE_FILE.read_bytes())
    if not scope or len({law.name for law in scope}) != len(scope) or len({law.folder for law in scope}) != len(scope):
        raise ValueError('Law scope must contain unique law names and folders')
    if any(law.folder in ('.', '..') for law in scope):
        raise ValueError('Law scope contains an invalid legalize-kr folder')
    return scope


def scope_keywords(scope: tuple[LawScope, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(word for law in scope for word in law.keywords))


def normalize_law_text(value: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', value).split()).casefold()
