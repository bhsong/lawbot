"""Interpret the stage of a bill from the Assembly's official observations."""

BILL_STATUS = ('PASS_GUBUN', 'CMT_PROC_RESULT_CD', 'LAW_PROC_RESULT_CD', 'PROC_RESULT_CD')
BILL_STAGES = (
    ('bill_decided', '본회의 의결'),
    ('judiciary_decided', '법사위 처리'),
    ('judiciary_presented', '법사위 상정'),
    ('judiciary_referred', '법사위 회부'),
    ('committee_decided', '소관위 처리'),
    ('committee_reviewed', '위원회 심사 처리'),
    ('committee_presented', '소관위 상정'),
    ('committee_referred', '소관위 회부'),
)
UNKNOWN_BILL_STATUS = '상태 확인 필요'


def bill_status(dates: dict[str, str], fields: dict[str, str]) -> str:
    if result := fields.get('PROC_RESULT_CD'):
        return result
    stage = next((label for role, label in BILL_STAGES if role in dates), '')
    return ' · '.join(filter(None, (fields.get('PASS_GUBUN'), stage))) or UNKNOWN_BILL_STATUS
