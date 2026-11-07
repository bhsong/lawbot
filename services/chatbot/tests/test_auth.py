import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request

from conftest import ADMIN_PW, ADMIN_USER, set_auth
from lawbot import envfile, settings, web
from lawbot.db import migrate
from lawbot_common import envfile as common_envfile


def test_scrypt_hash_roundtrip_and_rejects_bad_format(admin_hash):
    from lawbot import auth

    first, second = auth.hash_password(ADMIN_PW), auth.hash_password(ADMIN_PW)
    assert first != second and first.startswith('scrypt$16384$8$1$')
    assert auth.verify_password(ADMIN_PW, first) and not auth.verify_password('wrong', second)
    assert auth.verify_password(ADMIN_PW, admin_hash)
    for invalid in ('plain', admin_hash.replace('$16384$', '$32768$'), admin_hash[:-2], admin_hash + '$', '☃'):
        with pytest.raises(ValueError, match='LAWBOT_ADMIN_PW_HASH') as error:
            auth.verify_password(ADMIN_PW, invalid)
        assert invalid not in str(error.value)


@pytest.mark.parametrize('case', ['user-only', 'hash-only', 'same-user', 'no-db', 'invalid-hash'])
def test_admin_config_is_validated_at_startup(env, tmp_path, admin_hash, case):
    set_auth(env)
    path = tmp_path / 'private' / 'lawbot.db'
    migrate.init(path)
    if case != 'user-only':
        env.setenv('LAWBOT_ADMIN_PW_HASH', 'fixture-bad-hash' if case == 'invalid-hash' else admin_hash)
    if case != 'hash-only':
        env.setenv('LAWBOT_ADMIN_USER', 'tester' if case == 'same-user' else ADMIN_USER)
    if case != 'no-db':
        env.setenv('LAWBOT_DB', str(path))
    expected = {
        'user-only': 'LAWBOT_ADMIN_PW_HASH',
        'hash-only': 'LAWBOT_ADMIN_USER',
        'same-user': 'LAWBOT_USER',
        'no-db': 'LAWBOT_DB',
        'invalid-hash': 'LAWBOT_ADMIN_PW_HASH',
    }
    with pytest.raises(settings.SettingsError, match=expected[case]) as error:
        settings.load(web=True)
    assert all(secret not in str(error.value) for secret in (admin_hash, 'fixture-bad-hash', ADMIN_PW, ADMIN_USER))


def test_quoted_scrypt_hash_is_read_and_marked_secret(env, tmp_path, admin_hash, capsys):
    path = tmp_path / '.env'
    path.write_text(f"LAWBOT_ADMIN_PW_HASH='{admin_hash}'\n")
    path.chmod(0o644)
    assert common_envfile.read(path, common_envfile.lawbot_keys) == {'LAWBOT_ADMIN_PW_HASH': admin_hash}
    env.setenv('LAWBOT_ENV_FILE', str(path))
    assert envfile.load() == path
    output = capsys.readouterr().out
    assert 'LAWBOT_ADMIN_PW_HASH' in output and admin_hash not in output


def test_hash_command_prompts_twice(env, capsys):
    from lawbot import auth

    answers = iter([ADMIN_PW, ADMIN_PW])
    env.setattr(auth, 'getpass', lambda _: next(answers))
    auth.main(['hash'])
    output = capsys.readouterr().out.splitlines()
    assert len(output) == 1 and auth.verify_password(ADMIN_PW, output[0]) and ADMIN_PW not in output[0]


def test_login_failure_storage_is_bounded_and_expires():
    from lawbot import auth

    now = [0.0]
    limit = auth.AttemptWindow(5, 900, 4, cooldown=True, reserved=('admin',), clock=lambda: now[0])
    for _ in range(5):
        assert limit.allowed('admin')
        limit.record('admin')
    for i in range(100):
        unknown = f'unknown-{i}'
        if limit.allowed(unknown):
            limit.record(unknown)
    assert len(limit) <= 4 and not limit.allowed('admin')
    now[0] = 900
    assert limit.allowed('admin') and len(limit) == 0


def request_for(*, headers=(), chunks=(), app=None):
    async def receive():
        chunk = next(body, b'')
        return {'type': 'http.request', 'body': chunk, 'more_body': bool(chunk)}

    body = iter(chunks)
    return Request({'type': 'http', 'scheme': 'https', 'path': '/', 'headers': headers, 'app': app}, receive=receive)


def test_require_admin_uses_server_session():
    sessions = web.Sessions()
    app = SimpleNamespace(state=SimpleNamespace(sessions=sessions))
    with pytest.raises(HTTPException) as no_session:
        web.require_admin(request_for(app=app))
    assert no_session.value.status_code == 401
    for role in ('user', 'admin'):
        token = sessions.create('fixture', role)
        request = request_for(app=app, headers=[(b'cookie', f'{web.COOKIE}={token}'.encode())])
        if role == 'admin':
            assert web.require_admin(request)[2] == role
        else:
            with pytest.raises(HTTPException) as forbidden:
                web.require_admin(request)
            assert forbidden.value.status_code == 403


@pytest.mark.parametrize('origin', [None, b'http://fixture.example', b'https://other.example'])
def test_admin_origin_rejects_missing_or_mismatch(origin):
    headers = [(b'host', b'fixture.example')]
    if origin is not None:
        headers.append((b'origin', origin))
    with pytest.raises(HTTPException) as error:
        web.require_same_origin(request_for(headers=headers))
    assert error.value.status_code == 403 and error.value.code == 'bad_origin'


def test_admin_origin_accepts_exact_https_host():
    web.require_same_origin(
        request_for(headers=[(b'host', b'fixture.example'), (b'origin', b'https://fixture.example')])
    )


def test_admin_body_size_counts_streamed_bytes():
    body = b'x' * (64 * 1024)
    assert asyncio.run(web.read_admin_body(request_for(chunks=(body,)))) == body
    with pytest.raises(HTTPException) as error:
        asyncio.run(web.read_admin_body(request_for(chunks=(body, b'x'))))
    assert error.value.status_code == 413


def test_admin_post_limit_is_per_session_and_sliding():
    from lawbot import auth

    now = [0.0]
    limit = auth.AttemptWindow(30, 60, 200, clock=lambda: now[0])
    app = SimpleNamespace(state=SimpleNamespace(admin_post_limit=limit))
    req = request_for(app=app)

    async def exercise():
        who = ('fixture', 'one', 'admin')
        for _ in range(30):
            await web.require_admin_post_rate(req, who)
        with pytest.raises(HTTPException) as error:
            await web.require_admin_post_rate(req, who)
        assert error.value.status_code == 429
        await web.require_admin_post_rate(req, ('fixture', 'two', 'admin'))
        now[0] = 60
        await web.require_admin_post_rate(req, who)

    asyncio.run(exercise())
