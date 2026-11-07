import csv
import hashlib
import io
from codecs import BOM_UTF8
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from lawbot.paths import ROOT


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def source_name(path: Path) -> str:
    path = path.resolve()
    return str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


@dataclass(frozen=True)
class CsvLayout:
    columns: list[str]
    bom: bool
    newline: str
    final_newline: bool

    def render(self, rows: Iterable[Mapping[str, str]]) -> bytes:
        output = io.StringIO(newline='')
        writer = csv.writer(output, lineterminator=self.newline)
        writer.writerow(self.columns)
        writer.writerows([row[column] for column in self.columns] for row in rows)
        text = output.getvalue()
        if not self.final_newline:
            text = text.removesuffix(self.newline)
        return text.encode('utf-8-sig' if self.bom else 'utf-8')


@dataclass(frozen=True)
class CsvFile:
    path: Path
    layout: CsvLayout
    rows: list[dict[str, str]]
    checksum: str

    def metadata(self) -> dict:
        return {'path': source_name(self.path), 'sha256': self.checksum, 'layout': asdict(self.layout)}


def read_csv(path: Path, expected: Sequence[str], count: int | None = None) -> CsvFile:
    """Read UTF-8 CSV only if its rows and layout reproduce every input byte."""
    raw = path.read_bytes()
    text = raw.decode('utf-8-sig')
    newline = '\r\n' if text.partition('\n')[0].endswith('\r') else '\n'
    reader = csv.reader(io.StringIO(text, newline=''), strict=True)
    try:
        columns = next(reader, [])
        if len(columns) != len(expected) or set(columns) != set(expected):
            raise ValueError(f'{path.name} 열 구성이 예상과 다릅니다.')
        rows = []
        for values in reader:
            if len(values) != len(columns):
                raise ValueError(f'{path.name} {reader.line_num}행의 열 개수가 다릅니다.')
            rows.append(dict(zip(columns, values, strict=True)))
    except csv.Error as exc:
        raise ValueError(f'{path.name} {reader.line_num}행을 CSV로 읽을 수 없습니다.') from exc
    if count is not None and len(rows) != count:
        raise ValueError(f'{path.name} 행 수 {len(rows)}개가 예상 {count}개와 다릅니다.')
    layout = CsvLayout(columns, raw.startswith(BOM_UTF8), newline, text.endswith(newline))
    if layout.render(rows) != raw:
        raise ValueError(f'{path.name}의 인용 또는 줄바꿈을 바이트 단위로 복원할 수 없습니다.')
    return CsvFile(path, layout, rows, sha256(raw))
