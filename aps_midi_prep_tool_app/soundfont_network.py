"""Verified HTTPS for downloads, including standalone Windows builds."""

import ssl
import urllib.request

import certifi


def open_soundfont_url(request, *, timeout):
    # Frozen Python builds and freshly installed Windows systems may not have
    # downloaded all public CA roots yet. Keep OS/custom roots and supplement
    # them with Mozilla's bundled trust store; never disable TLS verification.
    context = ssl.create_default_context()
    context.load_verify_locations(cafile=certifi.where())
    return urllib.request.urlopen(request, timeout=timeout, context=context)
