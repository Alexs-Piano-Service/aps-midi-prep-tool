import hashlib
import io
import ssl
import urllib.error
import urllib.request
from unittest.mock import Mock

import pytest

from aps_midi_prep_tool_app import main_window, soundfont_network


@pytest.mark.parametrize("empty_system_roots", [False, True])
def test_https_supplements_system_roots_and_keeps_verification(monkeypatch, empty_system_roots):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT) if empty_system_roots else ssl.create_default_context()
    original_count = len(context.get_ca_certs())
    monkeypatch.setattr(soundfont_network.ssl, "create_default_context", lambda: context)
    urlopen = Mock()
    monkeypatch.setattr(soundfont_network.urllib.request, "urlopen", urlopen)
    request = urllib.request.Request("https://ftp.osuosl.org/MuseScore_General.sf3")
    soundfont_network.open_soundfont_url(request, timeout=30)
    urlopen.assert_called_once_with(request, timeout=30, context=context)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname
    assert len(context.get_ca_certs()) >= original_count
    assert context.get_ca_certs()


def test_certificate_failures_are_never_retried_without_verification(monkeypatch):
    error = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    urlopen = Mock(side_effect=error)
    monkeypatch.setattr(soundfont_network.urllib.request, "urlopen", urlopen)
    with pytest.raises(urllib.error.URLError):
        soundfont_network.open_soundfont_url("https://invalid.example/font.sf3", timeout=30)
    assert urlopen.call_count == 1
    assert urlopen.call_args.kwargs["context"].verify_mode == ssl.CERT_REQUIRED


class DownloadResponse(io.BytesIO):
    def __init__(self, payload, total=None):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload) if total is None else total)}


@pytest.mark.parametrize("failure", [None, "truncated", "hash", "certificate"])
def test_soundfont_download_publishes_only_complete_verified_payload(tmp_path, monkeypatch, failure):
    payload = b"RIFFtest-soundfont-data"
    output = tmp_path / "MuseScore_General.sf3"
    output.write_bytes(b"existing font")
    monkeypatch.setattr(main_window, "_downloaded_soundfont_path", lambda filename: str(output))
    opener = Mock(return_value=DownloadResponse(payload, len(payload) + 10 if failure == "truncated" else None))
    if failure == "certificate":
        opener.side_effect = urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed"))
    monkeypatch.setattr(main_window, "open_soundfont_url", opener)
    entry = {
        "url": "https://ftp.osuosl.org/pub/musescore/soundfont/MuseScore_General/MuseScore_General.sf3",
        "filename": output.name,
        "sha256": "bad" if failure == "hash" else hashlib.sha256(payload).hexdigest(),
    }
    worker = main_window.SoundFontDownloadWorker(entry)
    ready, errors = [], []
    worker.downloadFinished.connect(ready.append)
    worker.downloadFailed.connect(errors.append)
    worker.run()
    if failure:
        assert errors and not ready
        assert output.read_bytes() == b"existing font"
    else:
        assert not errors and ready == [str(output)]
        assert output.read_bytes() == payload
    assert not output.with_suffix(".sf3.download").exists()
