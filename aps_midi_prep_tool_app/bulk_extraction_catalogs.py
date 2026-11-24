"""Build folder-local Yamaha MIDI catalogs from verified extraction outputs."""

import os
from collections import defaultdict

from .dos83_renamer import build_dos83_midi_filename, is_dos83_filename
from .eseq_pianodir import (
    _pianodir_subset_template, decode_dos83_name, parse_pianodir_metadata,
)
from .helpers.atomic_file import atomic_write_bytes
from .helpers.portable_filename import is_windows_device_name
from .midi_metadata import normalize_title_spacing, read_first_title_from_midi
from .smart_pianosoft import (
    SmartPianoSoftMetadata,
    build_smart_pianosoft_song_catalog, build_smart_pianosoft_song_record,
    parse_smart_pianosoft_disk_title, parse_smart_pianosoft_song_catalog,
    update_smart_pianosoft_disk_title,
)


CATALOG_NAMES = {"PSONG.MNG", "PDISK.MNG"}


def available_yamaha_midi_path(directory, parts, reserved_keys, *, counter):
    """Allocate an actual portable DOS name, including when source names collide."""
    original = os.path.splitext(parts[-1])[0] + ".MID"
    candidate = original.upper() if is_dos83_filename(original) and not is_windows_device_name(original) else ""
    while True:
        if not candidate:
            candidate = build_dos83_midi_filename(parts[-1], counter)
            counter += 1
        relative = (*parts[:-1], candidate)
        key = "/".join(relative).casefold()
        path = os.path.join(directory, *relative)
        if key not in reserved_keys and not os.path.lexists(path):
            reserved_keys.add(key)
            return path
        candidate = ""


class ExtractedMidiCatalogs:
    def __init__(self, session, entries, *, fallback_title, trim_title_spaces=False,
                 generate_yamaha_catalogs=True):
        self.fallback_title = fallback_title
        self.trim_title_spaces = trim_title_spaces
        self.generate_yamaha_catalogs = generate_yamaha_catalogs
        self.metadata = {}
        self.outputs = defaultdict(list)
        for entry in entries:
            parts = str(entry.path).replace("\\", "/").split("/")
            name = parts[-1].upper()
            if name not in CATALOG_NAMES | {"PIANODIR.FIL"}:
                continue
            parent = "/".join(parts[:-1]).casefold()
            metadata = {}
            # Invalid optional catalogs do not prevent extracting valid songs.
            try:
                with open(session.extract_file(entry.path), "rb") as handle:
                    payload = handle.read()
                if name == "PSONG.MNG":
                    songs = parse_smart_pianosoft_song_catalog(payload)
                    metadata["song_template"] = payload
                    metadata["midi_songs"] = {
                        song.filename.casefold(): (index, song.title, song.raw_record)
                        for index, song in enumerate(songs)
                    }
                elif name == "PDISK.MNG":
                    metadata["disk_title"] = parse_smart_pianosoft_disk_title(payload)
                    metadata["disk_template"] = payload
                else:
                    records, _label = _pianodir_subset_template(payload)
                    metadata["eseq_disk_title"] = parse_pianodir_metadata(payload).disk_title
                    metadata["eseq_songs"] = {
                        decode_dos83_name(record[:11]).casefold(): (
                            index, record[0x30:0x50].split(b"\0", 1)[0].decode("cp1252", errors="replace").rstrip(), b"",
                        ) for index, record in enumerate(records)
                    }
            except (OSError, ValueError):
                continue
            self.metadata.setdefault(parent, {}).update(metadata)

    def applies_to(self, source):
        parent = os.path.dirname(str(source).replace("\\", "/")).casefold()
        return self.generate_yamaha_catalogs or parent in self.metadata

    @property
    def source_catalogs(self):
        return {
            directory: sorted(
                name for name, key in (("PSONG.MNG", "song_template"),
                                      ("PDISK.MNG", "disk_template"),
                                      ("PIANODIR.FIL", "eseq_songs"))
                if key in metadata
            ) for directory, metadata in self.metadata.items()
        }

    def _catalog_names(self, metadata):
        if self.generate_yamaha_catalogs:
            return CATALOG_NAMES
        return SmartPianoSoftMetadata(
            song_catalog=metadata.get("song_template", b""),
            disk_catalog=metadata.get("disk_template", b""),
            pianodir_source="eseq_songs" in metadata,
        ).source_catalog_names

    def _metadata(self, source):
        parent = os.path.dirname(str(source).replace("\\", "/")).casefold()
        return self.metadata.get(parent, {})

    def song_info(self, source):
        metadata = self._metadata(source)
        name = os.path.basename(str(source).replace("\\", "/")).casefold()
        midi_songs = metadata.get("midi_songs", {})
        if name in midi_songs:
            return midi_songs[name]
        eseq_song = metadata.get("eseq_songs", {}).get(name)
        if eseq_song is not None:
            return len(midi_songs) + eseq_song[0], eseq_song[1], eseq_song[2]
        return None

    def add(self, source, output, *, index):
        if not self.applies_to(source):
            return
        with open(output, "rb") as handle:
            header = handle.read(14)
        if header[:4] != b"MThd":
            return
        midi_format = int.from_bytes(header[8:10], "big")
        info = self.song_info(source)
        title = info[1] if info is not None else read_first_title_from_midi(output)
        title = title or os.path.splitext(os.path.basename(source))[0]
        if self.trim_title_spaces:
            title = normalize_title_spacing(title)
        record = build_smart_pianosoft_song_record(
            os.path.basename(output), title, source_record=info[2] if info is not None else b"",
            midi_format=midi_format,
        )
        order = (0, info[0], index) if info is not None else (1, index, index)
        self.outputs[os.path.dirname(output)].append((order, record, self._metadata(source)))

    def publish(self, *, job=None, image_name="", check_cancelled=lambda: None):
        for directory, songs in self.outputs.items():
            songs.sort(key=lambda song: song[0])
            metadata = songs[0][2]
            title = metadata.get("disk_title") or metadata.get("eseq_disk_title") or self.fallback_title
            names = self._catalog_names(metadata)
            payloads = {}
            if "PSONG.MNG" in names:
                payloads["PSONG.MNG"] = build_smart_pianosoft_song_catalog(
                    metadata.get("song_template", b""), [song[1] for song in songs],
                )
            if "PDISK.MNG" in names:
                payloads["PDISK.MNG"] = update_smart_pianosoft_disk_title(metadata.get("disk_template", b""), title)
            for name, payload in payloads.items():
                check_cancelled()
                destination = os.path.join(directory, name)
                with os.scandir(directory) as entries:
                    if any(entry.name.casefold() == name.casefold() and entry.name != name for entry in entries):
                        raise FileExistsError(f"A different catalog already exists: {destination}")
                if job is not None:
                    job.safe_output_path(destination)
                    if job.catalog_matches(image_name, destination, payload):
                        continue
                # Fixed catalog filenames cannot be made unique without breaking
                # playback. Preserve conflicting files and report the collision.
                atomic_write_bytes(destination, payload, replace_existing=False)
                if job is not None:
                    job.checkpoint_catalog(image_name, destination)
        if job is not None:
            job.image(image_name)["catalogs_complete"] = True
            job.save()
