from datetime import timedelta

from source_hub import cli, materials, nts
from source_hub.config import load
from source_hub.store import Registry
from test_moel_rss import NOW, setting


def test_nts_dated_list_and_off(tmp_path, monkeypatch):
    registry = Registry(load(), tmp_path / 'registry.db')
    path = tmp_path / 'materials.db'
    calls = []
    html = b"""<div class="bbs_ListA"><table><tbody><tr><td data-table="subject">
      <a class="nttInfoBtn" data-id="123" title="Official notice"><span>N</span></a></td>
      <td data-table="date">2026.11.13.</td></tr></tbody></table></div>"""

    def read(url, media, limit):
        calls.append(url)
        return b'User-agent: *\nAllow: /' if url.endswith('/robots.txt') else html

    monkeypatch.setattr(nts, 'read_response', read)
    assert nts.collect(registry, path, 'nts-notice', now=NOW)['outcome'] == 'disabled'
    assert not calls
    setting(registry, 'source', 'nts-notice', True)
    assert nts.collect(registry, path, 'nts-notice', now=NOW)['baseline_count'] == 1
    [doc] = materials.documents(path, 'nts-notice')
    assert doc['published_on'] == '2026-11-13'
    assert doc['canonical_url'] == nts.HOST + '/nts/na/ntt/selectNttInfo.do?mi=2207&nttSn=123'
    assert len(calls) == 2
    assert nts.collect(registry, path, 'nts-notice', now=NOW + timedelta(days=1))['new_count'] == 0


def test_booklet_metadata_uses_public_guide_link():
    data = """<div class="totalWrap"><a href="/comm/nttFileDownload.do?fileKey=private-token">
      <span>2025년 연말정산 신고안내(책자)</span></a></div>""".encode()
    [doc] = nts.parse_guide(data, 'nts-tax-02')
    assert doc.published_on is None
    assert doc.canonical_url == nts.HOST + nts.GUIDE
    assert 'private-token' not in repr(doc)


def test_hometax_is_listed_without_a_collection_route(tmp_path):
    registry = Registry(load(), tmp_path / 'registry.db')
    sources = [row for row in registry.snapshot()['sources'] if row['agency'] == 'hometax']
    assert sources
    assert all(row['verification'] == 'manual_review' and not row['runnable'] for row in sources)
    assert all(row['source_id'] not in cli.COLLECTORS for row in sources)
