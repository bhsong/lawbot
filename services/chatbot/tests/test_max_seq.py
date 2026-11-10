# tests/test_max_seq.py  →  조문 CSV의 임베딩 최대 토큰 (lawbot_tools.max_seq, v4 노트북 규칙: 최장 조문을 256 단위로 올림)
import pandas as pd

from lawbot_tools import max_seq


def test_round_up_like_the_v4_notebook():
    """v4: 법률 최장 2,088 → 2,304, 하위법령 최장 3,017 → 3,072 (deploy 기본값)"""
    assert max_seq.round_up(2088) == 2304 and max_seq.round_up(3017) == 3072 and max_seq.round_up(2304) == 2304


def test_settings_split_laws_and_decrees_and_skip_deleted():
    df = pd.DataFrame(
        {
            'law': ['가법', '가법', '가법 시행령'],
            'article_no': ['제1조', '제2조', '제1조'],
            'article_id': ['가법 제1조', '가법 제2조', '가법 시행령 제1조'],
            'title': ['목적', '삭제', '목적'],
            'text': ['짧음', '아주 긴 삭제 조문' * 100, '조금 더 긴 본문'],
            'deleted': [0, 1, 0],
            'kind': ['법률', '법률', '시행령'],
        }
    )
    got = max_seq.settings(df, lambda t: len(t))  # 글자 수를 토큰 수로 보는 가짜 토크나이저
    assert got == {'LAWBOT_MAX_SEQ': 256, 'LAWBOT_DECREE_MAX_SEQ': 256}
    assert max_seq.longest(df, lambda t: len(t)) == {
        '법률': ('가법 제1조', len('가법 제1조(목적)\n짧음')),
        '하위법령': ('가법 시행령 제1조', len('가법 시행령 제1조(목적)\n조금 더 긴 본문')),
    }


def test_counts_table_for_summaries():
    """요약이 토크나이저 없이 정답 조문 길이 구간을 나누도록 조문별 토큰 수 (data/v5/doc_tokens_v5.csv)"""
    df = pd.DataFrame(
        {
            'law': ['가법', '가법 시행령'],
            'article_no': ['제1조', '제1조'],
            'article_id': ['가법 제1조', '가법 시행령 제1조'],
            'title': ['목적', '목적'],
            'text': ['짧음', '본문'],
            'deleted': [0, 0],
            'kind': ['법률', '시행령'],
        }
    )
    got = max_seq.counts(df, lambda t: len(t))
    assert got.to_dict('records') == [
        {'article': '가법 제1조', 'group': '법률', 'tokens': len('가법 제1조(목적)\n짧음')},
        {'article': '가법 시행령 제1조', 'group': '하위법령', 'tokens': len('가법 시행령 제1조(목적)\n본문')},
    ]


def test_training_cap_reports_clipped_documents_and_preserves_raw_counts(tmp_path, capsys):
    from tokenizers import Tokenizer
    from tokenizers.models import WordLevel
    from tokenizers.pre_tokenizers import WhitespaceSplit

    tokenizer = tmp_path / 'tokenizer.json'
    tok = Tokenizer(WordLevel({'[UNK]': 0}, unk_token='[UNK]'))
    tok.pre_tokenizer = WhitespaceSplit()
    tok.save(str(tokenizer))
    source, report = tmp_path / 'articles.csv', tmp_path / 'counts.csv'
    pd.DataFrame(
        {
            'law': ['가법', '가법 시행령', '가법 시행령', '가법 시행령'],
            'article_no': ['제1조', '제1조', '제2조', '제3조'],
            'title': ['목적'] * 4,
            'text': ['본문', '본문 ' * 297, '본문 ' * 301, '본문 ' * 400],
            'deleted': [0, 0, 0, 1],
            'kind': ['법률', '시행령', '시행령', '시행령'],
        }
    ).to_csv(source, index=False)

    assert (
        max_seq.main([str(source), '--tokenizer', str(tokenizer), '--training-max-seq', '300', '--counts', str(report)])
        == 0
    )

    output = capsys.readouterr().out.splitlines()
    assert 'LAWBOT_MAX_SEQ=256' in output
    assert 'LAWBOT_DECREE_MAX_SEQ=300' in output
    assert [line for line in output if '잘림' in line] == [
        '# 하위법령 잘림: 가법 시행령 제2조 304토큰 중 4토큰 (1.32%)'
    ]
    assert pd.read_csv(report)['tokens'].tolist() == [3, 300, 304]
