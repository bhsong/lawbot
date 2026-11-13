from datetime import timedelta

from source_hub import cli, mofe
from source_hub.config import load
from source_hub.store import Registry
from test_moel_rss import NOW, setting

SOURCE = 'mofe-rss-press'
HOST = 'https://mofe.go.kr'
RSS = HOST + '/com/detailRssTagService.do?bbsId=MOSFBBS_000000000028'
BOARD = HOST + '/nw/nes/nesdta.do?bbsId=MOSFBBS_000000000028&menuNo=4010100'
ARTICLE = (
    HOST
    + '/nw/nes/detailNesDtaView.do?searchBbsId=MOSFBBS_000000000028&menuNo=4010100&searchNttId=MOSF_000000000000001'
)


def feed(link=None):
    link = link or ARTICLE.replace('https:', 'http:')
    return (
        '<rss><channel><item><title>보도자료</title><link>' + link.replace('&', '&amp;') + '</link>'
        '<pubDate>2026-11-13 09:00:00.0</pubDate><author>Ignore</author></item></channel></rss>'
    ).encode()


def test_official_feed_and_board_share_document_ids(tmp_path, monkeypatch):
    registry = Registry(load(), tmp_path / 'registry.db')
    database = tmp_path / 'materials.db'
    calls = []
    bodies = {
        HOST + '/robots.txt': b'User-agent: *\nDisallow: /sch/',
        RSS: feed(),
        BOARD: b"""<ul class="boardType3 explnList"><li><h3>
        <a href="javascript:fn_egov_select('MOSF_000000000000001','MOSFBBS_000000000028');">Corrected</a>
        </h3><span class="date">2026.11.13.</span></li><li><h3>
        <a href="javascript:fn_egov_select('MOSF_000000000000002');">Board only</a>
        </h3><span class="date">2026.11.12.</span></li></ul>""",
    }

    def read(url, media, limit):
        calls.append(url)
        return bodies[url]

    monkeypatch.setattr(mofe, 'read_response', read)
    assert mofe.collect(registry, database, SOURCE, now=NOW) == {'outcome': 'disabled'}
    assert calls == []
    setting(registry, 'source', SOURCE, True)
    result = mofe.collect(registry, database, SOURCE, now=NOW, reconcile=True)
    assert result['baseline_count'] == 2
    assert result['reconciliation']['board_only_count'] == 1
    assert calls == [HOST + '/robots.txt', RSS, BOARD]
    assert cli.main(['--database', str(registry.database), 'documents', SOURCE, '--materials', str(database)]) == 0
    body = mofe.documents(database, SOURCE)
    assert body[0]['canonical_url'] == ARTICLE
    assert body[0]['title'] == 'Corrected'
    assert body[0]['published_on'] == '2026-11-13'
    bodies[RSS] = feed(ARTICLE.replace('mofe.go.kr', 'other.example'))
    result = mofe.collect(registry, database, SOURCE, now=NOW + timedelta(days=1))
    assert result['outcome'] == 'error'
    assert mofe.documents(database, SOURCE) == body
