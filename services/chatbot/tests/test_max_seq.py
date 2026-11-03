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
