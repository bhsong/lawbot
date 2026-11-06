"""Verify and rasterize the official comparison table without altering its text."""

from contextlib import closing
from io import BytesIO
from typing import Literal

import pypdfium2 as pdfium
from pypdfium2 import raw

MAX_PAGES = 150
MAX_IMAGE_BYTES = 2 * 1024 * 1024
LimitReason = Literal['page_limit', 'image_limit']


class ComparisonLimitError(ValueError):
    """The official attachment exceeds a fixed comparison processing limit."""

    def __init__(self, reason: LimitReason) -> None:
        super().__init__(reason)
        self.reason: LimitReason = reason


def _table_dividers(page, width: float) -> list[float]:
    dividers = []
    for obj in page.get_objects(filter=[raw.FPDF_PAGEOBJ_PATH], max_depth=1):
        left, bottom, right, top = obj.get_bounds()
        center = (left + right) / 2
        if abs(center - width / 2) < width * 0.06 and right - left <= 5 and top - bottom >= 16:
            dividers.append(center / width)
    return dividers


def comparison_images(payload: bytes) -> list[tuple[int, bytes]]:
    """Return comparison pages or raise if the PDF cannot be opened or exceeds a processing limit."""
    images = []
    with pdfium.PdfDocument(payload) as document:
        if len(document) > MAX_PAGES:
            raise ComparisonLimitError('page_limit')
        previous_dividers: list[float] = []
        for index in range(len(document)):
            with closing(document[index]) as page, closing(page.get_textpage()) as text:
                compact = ''.join(text.get_text_bounded().split()).translate(str.maketrans('', '', '·ㆍ'))
                width, height = page.get_size()
                if width <= 0 or height <= 0:
                    previous_dividers = []
                    continue
                dividers = _table_dividers(page, width)
                heading = '신구조문대비표현행개정안' in compact[:140]
                continued = any(abs(left - right) <= 0.04 for left in previous_dividers for right in dividers)
                if heading or (compact and continued):
                    with closing(page.render(scale=min(2, 1400 / width, 1900 / height), draw_annots=False)) as bitmap:
                        with BytesIO() as buffer:
                            bitmap.to_pil().save(buffer, format='PNG')
                            png = buffer.getvalue()
                    if len(png) > MAX_IMAGE_BYTES:
                        raise ComparisonLimitError('image_limit')
                    images.append((index + 1, png))
                    previous_dividers = dividers
                else:
                    previous_dividers = []
    return images
