"""Keep standalone calendar tests independent of chatbot fixtures and configuration."""

import socket
from urllib.parse import urlsplit

import pytest


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_makereport(item, call):
    report = yield
    setattr(item, f'rep_{report.when}', report)
    return report


@pytest.fixture
def calendar_network(monkeypatch):
    connect = socket.socket.connect

    def local_connect(sock, address):
        assert isinstance(address, tuple) and address[0] == '127.0.0.1', 'Only local test servers are allowed'
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, 'connect', local_connect)


@pytest.fixture(scope='module')
def calendar_browser():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def calendar_pages(calendar_browser):
    contexts = []
    api_requests = []
    csp_errors = []

    def open_page(**options):
        context = calendar_browser.new_context(locale='ko-KR', **options)
        contexts.append(context)
        context.on(
            'request',
            lambda request: api_requests.append(request.url) if '/api/' in urlsplit(request.url).path else None,
        )
        context.on(
            'console',
            lambda message: (
                csp_errors.append(message.text)
                if message.type == 'error' and 'content security policy' in message.text.lower()
                else None
            ),
        )
        context.route(
            '**/*',
            lambda route: route.continue_() if route.request.url.startswith('http://127.0.0.1:') else route.abort(),
        )
        return context.new_page()

    try:
        yield open_page
        assert api_requests == []
        assert csp_errors == []
    finally:
        for context in contexts:
            context.close()


@pytest.fixture
def calendar_page(calendar_pages):
    return calendar_pages()
