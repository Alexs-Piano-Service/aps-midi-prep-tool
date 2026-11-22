"""A cancellable handoff from media workers to the user's consent dialog."""

import threading


class YamahaConsentRequest:
    def __init__(self):
        self.approved = False
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def resolve(self, approved):
        # Cancellation wins if it resolved the request while the dialog was open.
        with self._lock:
            if not self._ready.is_set():
                self.approved = bool(approved)
                self._ready.set()

    def wait(self, timeout=None):
        return self._ready.wait(timeout)


def session_requires_yamaha_consent(session, listing):
    """Recognize protection or complete Smart fingerprints before handing off a read."""
    from .smart_pianosoft import parse_smart_pianosoft_song_catalog
    from .smart_pianosoft_sync import parse_sps_midi

    if any((report.get("sector_map") or {}).get("expected_yamaha_protection", 0)
           for report in (getattr(session, "gw_sector_reports", ()) or ())):
        return True
    entries = {entry.path.rsplit("/", 1)[-1].casefold(): entry.path
               for entry in listing.entries if not entry.is_directory and not entry.directory}
    if (getattr(session, "source_boot_sector_repaired", False)
            and {"pianodir.fil", "psong.mng"}.intersection(entries)):
        return True
    if "psong.mng" not in entries:
        return False
    try:
        with open(session.extract_file(entries["psong.mng"]), "rb") as source:
            songs = parse_smart_pianosoft_song_catalog(source.read())
        if not songs:
            return False
        for song in songs:
            path = entries.get(song.filename.casefold())
            if path is None:
                return False
            parse_sps_midi(session.extract_file(path))
    except (ValueError, OSError):
        return False
    return True
