"""Administrator credentials and bounded, in-memory attempt windows."""

import argparse
import base64
import hashlib
import hmac
import secrets
import time
import warnings
from collections import OrderedDict, deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from getpass import GetPassWarning, getpass
from typing import Literal

Role = Literal['user', 'admin']
N, R, P = 2**14, 8, 1
SALT_BYTES, DIGEST_BYTES = 16, 32
HASH_PREFIX = f'scrypt${N}${R}${P}$'
HASH_ERROR = 'LAWBOT_ADMIN_PW_HASH의 scrypt 형식 또는 매개변수가 올바르지 않습니다.'


@dataclass(frozen=True)
class ScryptHash:
    salt: bytes = field(repr=False)
    digest: bytes = field(repr=False)


DUMMY_HASH = ScryptHash(bytes(SALT_BYTES), bytes(DIGEST_BYTES))


def parse_password_hash(encoded: str) -> ScryptHash:
    """Validate the fixed scrypt work factors and base64 field lengths."""
    if not encoded.startswith(HASH_PREFIX) or len(encoded) != len(HASH_PREFIX) + 24 + 1 + 44:
        raise ValueError(HASH_ERROR)
    salt64, separator, digest64 = encoded[len(HASH_PREFIX) :].partition('$')
    try:
        salt = base64.b64decode(salt64, validate=True)
        digest = base64.b64decode(digest64, validate=True)
    except ValueError:
        raise ValueError(HASH_ERROR) from None
    if not separator or (len(salt), len(digest)) != (SALT_BYTES, DIGEST_BYTES):
        raise ValueError(HASH_ERROR)
    return ScryptHash(salt, digest)


def _derive(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=DIGEST_BYTES)


def hash_password(password: str) -> str:
    """Encode a password with the supported scrypt parameters and a fresh salt."""
    salt = secrets.token_bytes(SALT_BYTES)
    salt64 = base64.b64encode(salt).decode('ascii')
    digest64 = base64.b64encode(_derive(password, salt)).decode('ascii')
    return f'{HASH_PREFIX}{salt64}${digest64}'


def verify_password(password: str, encoded: str | ScryptHash) -> bool:
    """Compare a derived digest, raising ValueError for malformed encoded hashes."""
    stored = parse_password_hash(encoded) if isinstance(encoded, str) else encoded
    return hmac.compare_digest(_derive(password, stored.salt), stored.digest)


@dataclass(frozen=True)
class AdminCredentials:
    user: str
    password_hash: ScryptHash = field(repr=False)


def admin_credentials(env: Mapping[str, str], *, db_configured: bool) -> AdminCredentials | None:
    user = (env.get('LAWBOT_ADMIN_USER') or '').strip()
    encoded = (env.get('LAWBOT_ADMIN_PW_HASH') or '').strip()
    if not user and not encoded:
        return None
    if not user or not encoded:
        raise ValueError('LAWBOT_ADMIN_USER와 LAWBOT_ADMIN_PW_HASH를 함께 설정하세요.')
    if user == (env.get('LAWBOT_USER') or '').strip():
        raise ValueError('LAWBOT_ADMIN_USER는 LAWBOT_USER와 달라야 합니다.')
    if not db_configured:
        raise ValueError('관리자 계정을 쓰려면 LAWBOT_DB를 설정하세요.')
    return AdminCredentials(user, parse_password_hash(encoded))


class AttemptWindow:
    """Bound recorded keys and counts. Call record only after an allowed check."""

    def __init__(
        self,
        limit: int,
        seconds: int,
        capacity: int,
        *,
        cooldown: bool = False,
        reserved: Iterable[str] = (),
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limit, self.seconds, self.capacity = limit, seconds, capacity
        self.cooldown, self.reserved, self.clock = cooldown, frozenset(reserved), clock
        self.entries: OrderedDict[str, deque[float]] = OrderedDict()

    def __len__(self) -> int:
        return len(self.entries)

    def allowed(self, key: str) -> bool:
        cutoff = self.clock() - self.seconds
        while self.entries and next(iter(self.entries.values()))[-1] <= cutoff:
            self.entries.popitem(last=False)
        attempts = self.entries.get(key)
        if attempts is None:
            return key in self.reserved or len(self.entries) < self.capacity - len(
                self.reserved.difference(self.entries)
            )
        if self.cooldown and len(attempts) == self.limit:
            return False
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        return len(attempts) < self.limit

    def record(self, key: str) -> None:
        if key not in self.entries:
            self.entries[key] = deque(maxlen=self.limit)
        self.entries[key].append(self.clock())
        self.entries.move_to_end(key)

    def clear(self, key: str) -> None:
        self.entries.pop(key, None)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='관리자 비밀번호 해시 만들기')
    parser.add_argument('command', choices=['hash'])
    parser.parse_args(argv)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', GetPassWarning)
            password = getpass('관리자 비밀번호: ')
            confirmation = getpass('관리자 비밀번호 확인: ')
    except EOFError, GetPassWarning:
        parser.exit(2, '비밀번호를 입력할 터미널이 필요합니다.\n')
    if not password or password != confirmation:
        parser.exit(2, '비밀번호가 비어 있거나 두 입력이 일치하지 않습니다.\n')
    print(hash_password(password))


if __name__ == '__main__':
    main()
