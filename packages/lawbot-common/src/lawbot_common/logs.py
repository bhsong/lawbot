# lawbot_common/logs.py  →  서비스 로그: logging으로 '[태그] 메시지' 한 줄씩 표준 출력에 (launchd 로그 파일에 그대로 쌓임)
# 형식은 예전 print 출력과 같다 (운영 로그를 보는 법이 바뀌지 않게). 질문 원문·토큰 같은 값은 로그에 넣지 않는다
import logging
import sys


class _Stdout(logging.StreamHandler):
    """지금의 sys.stdout에 쓴다 (테스트의 출력 캡처·launchd의 리디렉션 모두 그대로 따라감)"""

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, value):  # StreamHandler.__init__이 대입함: 무시
        pass


def logger(name, tag):
    """name 로거 ('lawbot'·'lawbot_model_server'). 처음 부를 때 '[tag] 메시지' 처리기를 붙인다 (INFO 이상)"""
    log = logging.getLogger(name)
    if not any(isinstance(h, _Stdout) for h in log.handlers):
        h = _Stdout()
        h.setFormatter(logging.Formatter(f'[{tag}] %(message)s'))
        log.addHandler(h)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log
