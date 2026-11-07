from lawbot.qa_rules import LEAK_SPLITS, OK_TO_VERDICT, Overlap, latest_rows, norm


def test_norm_keeps_letters_and_digits():
    assert norm('소득세법 제20조, Tax_2026?') == '소득세법제20조tax2026'


def test_overlap_prefers_exact_and_uses_inclusive_similarity_threshold():
    overlap = Overlap([('v4_test', 'Q0001', 'A123456789'), ('oos', 'OOS001', 'A 123456789')])
    assert overlap.match('a_123456789?') == ('v4_test:Q0001', 'exact')
    assert overlap.match('b123456789') == ('v4_test:Q0001', 0.9)
    assert overlap.match('zz12345678') is None


def test_latest_rows_uses_timestamp_then_input_order_and_drops_cleared_turns():
    def row(feedback_id, turn_id, ts, label='like'):
        return dict(feedback_id=feedback_id, turn_id=turn_id, ts=ts, label=label)

    newer = row('d2', 'd', '2026-11-01T10:00:05', 'flag')
    tied = row('f2', 'f', '2026-11-01T10:00:02', 'flag')
    rows = [
        newer,
        row('d1', 'd', '2026-11-01T10:00:00'),
        row('e1', 'e', '2026-11-01T10:00:01'),
        row('f1', 'f', '2026-11-01T10:00:02'),
        row('e2', 'e', '2026-11-01T10:00:03', 'cleared'),
        tied,
    ]
    assert latest_rows(rows) == ([newer, tied], 1)


def test_review_verdicts_and_leak_splits():
    assert OK_TO_VERDICT == {'맞음': 'correct', '틀림': 'incorrect', '모름': 'uncertain'}
    assert LEAK_SPLITS == ('val', 'test')
