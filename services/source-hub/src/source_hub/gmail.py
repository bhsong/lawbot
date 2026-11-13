"""Send UTF-8 multipart messages with Google's send-only Gmail API."""

import base64
from dataclasses import dataclass
from email.message import EmailMessage
from email.policy import SMTP
from typing import Literal, NamedTuple

import httpx

TOKEN_URL = 'https://oauth2.googleapis.com/token'
SEND_URL = 'https://gmail.googleapis.com/gmail/v1/users/me/messages/send'

DeliveryStatus = Literal['accepted', 'retryable', 'unknown', 'failed']


class Message(NamedTuple):
    subject: str
    text: str
    html: str


class Result(NamedTuple):
    status: DeliveryStatus
    provider_id: str | None = None


@dataclass(frozen=True, repr=False)
class Credentials:
    client_id: str
    client_secret: str
    refresh_token: str


def message(body: Message, sender: str, recipient: str, snapshot_hash: str) -> bytes:
    mail = EmailMessage(policy=SMTP)
    mail['Subject'] = body.subject
    mail['From'] = sender
    mail['To'] = recipient
    mail['X-ERP-Snapshot'] = snapshot_hash
    mail.set_content(body.text, charset='utf-8')
    mail.add_alternative(body.html, subtype='html', charset='utf-8')
    return mail.as_bytes()


class Gmail:
    def __init__(self, http: httpx.Client, credentials: Credentials):
        self.http = http
        self.credentials = credentials
        self._access = ''

    def _refresh(self) -> Result | None:
        try:
            response = self.http.post(
                TOKEN_URL,
                data={
                    'client_id': self.credentials.client_id,
                    'client_secret': self.credentials.client_secret,
                    'refresh_token': self.credentials.refresh_token,
                    'grant_type': 'refresh_token',
                },
            )
        except httpx.RequestError:
            return Result('retryable')
        if not response.is_success:
            return Result('retryable' if response.status_code == 429 or response.status_code >= 500 else 'failed')
        try:
            token = response.json()['access_token']
        except KeyError, TypeError, ValueError:
            return Result('failed')
        if not isinstance(token, str) or not token or not token.isascii() or any(c.isspace() for c in token):
            return Result('failed')
        self._access = token
        return None

    def send(self, mime: bytes) -> Result:
        if not self._access and (failure := self._refresh()):
            return failure
        try:
            response = self.http.post(
                SEND_URL,
                headers={'Authorization': f'Bearer {self._access}'},
                json={'raw': base64.urlsafe_b64encode(mime).decode('ascii')},
            )
        except httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout:
            return Result('retryable')
        except httpx.RequestError:
            return Result('unknown')
        if response.is_success:
            try:
                provider_id = response.json()['id']
            except KeyError, TypeError, ValueError:
                return Result('unknown')
            return (
                Result('accepted', provider_id) if isinstance(provider_id, str) and provider_id else Result('unknown')
            )
        if response.status_code == 401:
            self._access = ''
        if response.status_code in (401, 429):
            return Result('retryable')
        if response.status_code >= 500 or response.status_code == 408 or response.is_redirect:
            return Result('unknown')
        if response.status_code == 403:
            try:
                reason = response.json()['error']['errors'][0]['reason']
            except KeyError, IndexError, TypeError, ValueError:
                reason = None
            if reason in ('userRateLimitExceeded', 'rateLimitExceeded'):
                return Result('retryable')
        return Result('failed')
