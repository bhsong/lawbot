"""Recognize requests that arrived through the Cloudflare tunnel.

Cloudflare adds ``CF-Connecting-IP`` to every proxied request and clients cannot remove it, while
``tailscale serve`` and local requests never carry it. Administrator features stay tailnet-only by
refusing any request that has the header.
"""

from collections.abc import Mapping

EDGE_HEADER = 'cf-connecting-ip'


def via_cloudflare(headers: Mapping[str, str]) -> bool:
    """Return True when the request passed through the Cloudflare edge."""
    return EDGE_HEADER in headers
