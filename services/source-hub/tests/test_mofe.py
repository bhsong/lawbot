from datetime import timedelta

from source_hub import cli, mofe
from source_hub.config import load
from source_hub.store import Registry
from test_moel_rss import NOW, setting

SOURCE = 'mofe-laws'
HOST = 'https://mofe.go.kr'
BOARD = HOST + '/com/bbs/ComtnbbsList.do?bbsId=MOSFBBS_000000000059&menuNo=7080200'
ARTICLE = (
    HOST
    + '/com/bbs/detailComtnbbsView.do?searchBbsId1=MOSFBBS_000000000059&menuNo=7080200&searchNttId1=MOSF_000000000000001'
)


def test_official_board_collects_metadata_and_preserves_last_success(tmp_path, monkeypatch):
    registry = Registry(load(), tmp_path / 'registry.db')
    database = tmp_path / 'materials.db'
    calls = []
    bodies = {
        HOST + '/robots.txt': b'User-agent: *\nDisallow: /sch/',
        BOARD: b"""<ul class="boardType3 explnList"><li><h3>
        <a href="javascript:fn_egov_select('MOSF_000000000000001','MOSFBBS_000000000059');">Corrected</a>
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
    assert calls == [HOST + '/robots.txt', BOARD]
    assert cli.main(['--database', str(registry.database), 'documents', SOURCE, '--materials', str(database)]) == 0
    body = mofe.documents(database, SOURCE)
    assert body[0]['canonical_url'] == ARTICLE
    assert body[0]['title'] == 'Corrected'
    assert body[0]['published_on'] == '2026-11-13'
    bodies[BOARD] = b'<html>layout changed</html>'
    result = mofe.collect(registry, database, SOURCE, now=NOW + timedelta(days=1))
    assert result['outcome'] == 'error'
    assert mofe.documents(database, SOURCE) == body
