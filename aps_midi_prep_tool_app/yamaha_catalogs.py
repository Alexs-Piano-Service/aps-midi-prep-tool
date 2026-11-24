"""Read Yamaha album metadata across MIDI and E-SEQ source formats."""

from dataclasses import replace
import os

from .eseq_pianodir import (
    PIANODIR_FILENAME, _pianodir_subset_template, decode_dos83_name,
    parse_pianodir_metadata,
)
from .smart_pianosoft import (
    SmartPianoSoftMetadata, SmartPianoSoftSong, smart_pianosoft_metadata_from_directory,
)


def yamaha_metadata_from_pianodir_bytes(payload):
    """Read valid E-SEQ catalog metadata without inventing MIDI record fields.

    Raise ValueError for malformed PIANODIR data. This payload entry point also
    supports image extraction, whose temporary filenames need not be PIANODIR.
    """
    records, _label = _pianodir_subset_template(payload)
    album = parse_pianodir_metadata(payload)
    songs = tuple(
        SmartPianoSoftSong(index, filename, record[0x30:0x50].split(b"\0", 1)[0].decode(
            "cp1252", errors="replace",
        ).strip())
        for index, record in enumerate(records, start=1)
        if (filename := decode_dos83_name(record[:11]))
    )
    return SmartPianoSoftMetadata(disk_title=album.disk_title, songs=songs, pianodir_source=True)


def yamaha_catalog_metadata_from_directory(directory):
    """Read MIDI catalogs, filling missing metadata from a valid PIANODIR.

    E-SEQ entries retain their source filenames for conversion matching. Their
    MIDI catalog records must be built fresh; the E-SEQ record layout differs.
    Existing MIDI catalog payloads and opaque record fields stay intact.
    """
    metadata = smart_pianosoft_metadata_from_directory(directory)
    try:
        with os.scandir(directory) as entries:
            candidates = sorted(
                (entry for entry in entries
                 if entry.name.upper() == PIANODIR_FILENAME and entry.is_file(follow_symlinks=False)),
                key=lambda entry: (entry.name != entry.name.upper(), entry.name),
            )
    except OSError:
        return metadata
    for entry in candidates:
        try:
            with open(entry.path, "rb") as handle:
                payload = handle.read(1024 * 1024 + 1)
            if len(payload) > 1024 * 1024:
                continue
            eseq = yamaha_metadata_from_pianodir_bytes(payload)
        except (OSError, ValueError):
            continue
        midi_names = {song.filename.casefold() for song in metadata.songs}
        songs = tuple(song for song in eseq.songs if song.filename.casefold() not in midi_names)
        return replace(metadata, disk_title=metadata.disk_title or eseq.disk_title,
                       songs=metadata.songs + songs, pianodir_source=True)
    return metadata
