import csv
import hashlib
import ntpath
from pathlib import Path

import pytest

from aps_midi_prep_tool_app import emulator_image_builder
from aps_midi_prep_tool_app.emulator_image_builder import (
    DEFAULT_SAFETY_MARGIN_BYTES,
    build_emulator_disk_images,
    build_emulator_eseq_images,
    discover_midi_files,
    discover_song_files,
    sanitize_image_prefix,
    sanitize_image_set_name,
)
from aps_midi_prep_tool_app.eseq_converter import (
    convert_midi_file_to_eseq_path,
    is_eseq_file,
)
from aps_midi_prep_tool_app.eseq_pianodir import (
    PIANODIR_COUNT_OFFSET,
    PIANODIR_FILENAME,
    read_pianodir_metadata_from_file,
)
from aps_midi_prep_tool_app.floppy_image import (
    DISK_FORMATS,
    FloppyImageError,
    FloppyImageSession,
    FloppyOperationCancelled,
    allocated_size,
    create_blank_floppy_image,
    read_image_listing,
)
from aps_midi_prep_tool_app.midi_metadata import (
    extract_eseq_title_from_file,
    extract_first_title_from_midi,
    is_midi_file,
)
from aps_midi_prep_tool_app.smart_pianosoft import (
    parse_smart_pianosoft_disk_title,
    parse_smart_pianosoft_song_catalog,
)


def test_windows_report_paths_use_forward_slashes(monkeypatch):
    monkeypatch.setattr(emulator_image_builder.os.path, "relpath", ntpath.relpath)
    assert emulator_image_builder._display_relative_path(
        r"C:\Music\Billy Joel\SONG.MID", r"C:\Music",
    ) == "Billy Joel/SONG.MID"


def _midi_bytes(title):
    title_bytes = title.encode("ascii")
    track = (
        b"\x00\xff\x03"
        + bytes((len(title_bytes),))
        + title_bytes
        + b"\x00\x90\x3c\x40"
        + b"\x60\x80\x3c\x00"
        + b"\x00\xff\x2f\x00"
    )
    return (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (96).to_bytes(2, "big")
        + b"MTrk"
        + len(track).to_bytes(4, "big")
        + track
    )


def _vlq(value):
    encoded = [value & 0x7F]
    value >>= 7
    while value:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    return bytes(reversed(encoded))


def _large_midi_bytes(title, payload_size):
    title_bytes = title.encode("ascii")
    payload = b"x" * payload_size
    track = (
        b"\x00\xff\x03"
        + bytes((len(title_bytes),))
        + title_bytes
        + b"\x00\xff\x7f"
        + _vlq(len(payload))
        + payload
        + b"\x00\xff\x2f\x00"
    )
    return (
        b"MThd"
        + (6).to_bytes(4, "big")
        + (0).to_bytes(2, "big")
        + (1).to_bytes(2, "big")
        + (96).to_bytes(2, "big")
        + b"MTrk"
        + len(track).to_bytes(4, "big")
        + track
    )


def _disk_format(key="ibm.720"):
    return next(item for item in DISK_FORMATS if item.key == key)


def _write_index_csv(directory, rows):
    fieldnames = (
        "number",
        "output_folder",
        "output_file",
        "title",
        "source_path",
        "sha256",
    )
    with (directory / "INDEX.csv").open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_discovers_midi_files_recursively_in_natural_order(tmp_path):
    source = tmp_path / "source"
    folder_2 = source / "Folder 2"
    folder_10 = source / "Folder 10"
    grandchild = folder_2 / "grandchild"
    grandchild.mkdir(parents=True)
    folder_10.mkdir()
    (source / "Song 10.mid").write_bytes(_midi_bytes("Ten"))
    (source / "Song 2.MIDI").write_bytes(_midi_bytes("Two"))
    (folder_2 / "Song 3.mid").write_bytes(_midi_bytes("Three"))
    (folder_10 / "Song 1.mid").write_bytes(_midi_bytes("Folder ten"))
    (grandchild / "Song 4.mid").write_bytes(_midi_bytes("Four"))
    (grandchild / "ignore.txt").write_text("not MIDI", encoding="utf-8")
    (grandchild / "PIANODIR.FIL").write_bytes(b"directory metadata")

    discovered = discover_midi_files(source)

    assert [Path(path).relative_to(source).as_posix() for path in discovered] == [
        "Song 2.MIDI",
        "Song 10.mid",
        "Folder 2/Song 3.mid",
        "Folder 10/Song 1.mid",
        "Folder 2/grandchild/Song 4.mid",
    ]
    assert [Path(path).name for path in discover_midi_files(
        source,
        include_subfolders=False,
    )] == ["Song 2.MIDI", "Song 10.mid"]

    eseq_path = folder_2 / "Song 5.fil"
    convert_midi_file_to_eseq_path(source / "Song 2.MIDI", eseq_path)
    discovered_songs = discover_song_files(source)
    assert [Path(path).relative_to(source).as_posix() for path in discovered_songs] == [
        "Song 2.MIDI",
        "Song 10.mid",
        "Folder 2/Song 3.mid",
        "Folder 2/Song 5.fil",
        "Folder 10/Song 1.mid",
        "Folder 2/grandchild/Song 4.mid",
    ]


def test_sanitizes_portable_image_set_names():
    assert sanitize_image_set_name('  My: Emulator / Set?  ') == "My Emulator Set"
    assert sanitize_image_set_name("...") == "Emulator Disks"
    assert sanitize_image_prefix(" dsk-a ") == "DSKA"
    assert sanitize_image_prefix("library") == "LIBR"
    assert sanitize_image_prefix("---") == "DSKA"


@pytest.mark.parametrize("name", ["CON", "aux", "NUL", "PRN", "COM1", "LPT9", "CON.mid", "AUX.mid", "COM¹"])
def test_image_set_names_are_portable_windows_device_names(name):
    assert sanitize_image_set_name(name) == "_" + name


@pytest.mark.parametrize("include_subfolders", [False, True])
def test_discovers_songs_by_content_and_excludes_catalogs(tmp_path, include_subfolders):
    source = tmp_path / "songs"
    source.mkdir()
    midi_path = source / "FIRST"
    midi_path.write_bytes(_midi_bytes("First song"))
    eseq_path = source / "SECOND"
    convert_midi_file_to_eseq_path(midi_path, eseq_path)
    (source / "NOTES.FIL").write_bytes(b"This is not a song.")
    (source / "NOTES.MID").write_bytes(b"This is not MIDI.")
    # Directory/catalog files are never song entries, even if a damaged or
    # mislabeled catalog happens to contain a recognized song header.
    for name in ("PIANODIR.FIL", "MUSIC.DIR", "PDISK.MNG", "psong.mng"):
        (source / name).write_bytes(eseq_path.read_bytes())

    assert discover_song_files(source, include_subfolders=include_subfolders) == [
        str(midi_path), str(eseq_path),
    ]
    assert discover_midi_files(source, include_subfolders=include_subfolders) == [
        str(midi_path),
    ]


@pytest.mark.parametrize("include_subfolders", [False, True])
def test_song_discovery_ignores_backup_temp_and_hidden_copies(tmp_path, include_subfolders):
    source = tmp_path / "songs"
    source.mkdir()
    midi = source / "song.mid"
    midi.write_bytes(_midi_bytes("One intended song"))
    eseq = source / "song.fil"
    convert_midi_file_to_eseq_path(midi, eseq)
    for name in ("song.mid.bak", "song.mid.orig", "song.mid.tmp", "song.mid~", ".hidden", ".hidden.mid", "COPY~"):
        (source / name).write_bytes(midi.read_bytes())
    for name in ("song.fil~", "song.fil.orig", "song.fil.bak", "song.recovered", ".hidden.fil"):
        (source / name).write_bytes(eseq.read_bytes())
    hidden_folder = source / ".backup"
    hidden_folder.mkdir()
    (hidden_folder / "song.mid").write_bytes(midi.read_bytes())
    nested = source / "Album"
    nested.mkdir()
    (nested / "song.mid.bak").write_bytes(midi.read_bytes())
    (nested / "song.fil~").write_bytes(eseq.read_bytes())

    assert discover_song_files(source, include_subfolders=include_subfolders) == [str(eseq), str(midi)]


def test_builds_multiple_verified_images_with_a_pianodir_per_disk(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "midi"
    output = tmp_path / "images"
    source.mkdir()
    for index in range(1, 4):
        (source / f"Song {index}.mid").write_bytes(
            _midi_bytes(f"Song {index}")
        )

    monkeypatch.setattr(emulator_image_builder, "PIANODIR_MAX_TRACKS", 2)
    result = build_emulator_eseq_images(
        source,
        output,
        prefix="DSKB",
        starting_number=12,
        album_title="Customer Album",
        disk_format=_disk_format(),
        output_ext="img",
    )

    assert result.midi_files_found == 3
    assert result.converted_files == 3
    assert result.images_created == 2
    assert [Path(path).name for path in result.output_paths] == [
        "DSKB0012.img",
        "DSKB0013.img",
    ]
    assert result.image_prefix == "DSKB"
    assert result.starting_number == 12
    assert result.safety_margin_bytes == DEFAULT_SAFETY_MARGIN_BYTES

    song_counts = []
    collected_names = []
    for image_path in result.output_paths:
        listing = read_image_listing(image_path)
        names = [entry.name for entry in listing.entries]
        assert names.count(PIANODIR_FILENAME) == 1
        eseq_names = [name for name in names if name.upper().endswith(".FIL") and name != PIANODIR_FILENAME]
        song_counts.append(len(eseq_names))
        collected_names.extend(eseq_names)

        session = FloppyImageSession.load(image_path)
        try:
            pianodir_path = session.extract_file(PIANODIR_FILENAME)
            pianodir_bytes = Path(pianodir_path).read_bytes()
            assert int.from_bytes(
                pianodir_bytes[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2],
                "little",
            ) == len(eseq_names) + 1
            metadata = read_pianodir_metadata_from_file(pianodir_path)
            disk_number = int(Path(image_path).stem[-4:])
            assert metadata.catalog_number == f"DSKB-{disk_number:04d}"
            assert metadata.disk_title == "Customer Album"
            for eseq_name in eseq_names:
                assert is_eseq_file(session.extract_file(eseq_name))
        finally:
            session.cleanup()

    assert song_counts == [2, 1]
    assert len(set(collected_names)) == 3


def test_builds_midi_only_images_and_converts_eseq_only_when_needed(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    midi_path = source / "Already MIDI.mid"
    midi_path.write_bytes(_midi_bytes("Already MIDI"))
    eseq_path = source / "Needs Conversion.fil"
    convert_midi_file_to_eseq_path(
        midi_path,
        eseq_path,
        filename_hint="NEEDSCON.FIL",
    )

    # The E-SEQ directory limit must not split a MIDI disk set.
    monkeypatch.setattr(emulator_image_builder, "PIANODIR_MAX_TRACKS", 1)
    result = build_emulator_disk_images(
        source,
        output,
        set_name="MIDI Set",
        disk_format=_disk_format(),
        output_content="midi",
        output_ext="img",
    )

    assert result.song_files_found == 2
    assert result.files_prepared == 2
    assert result.converted_files == 1
    assert result.output_content == "midi"
    assert result.images_created == 1
    listing = read_image_listing(result.output_paths[0])
    names = [entry.name for entry in listing.entries]
    assert len(names) == 2
    assert PIANODIR_FILENAME not in names
    assert all(name.upper().endswith(".MID") for name in names)

    session = FloppyImageSession.load(result.output_paths[0])
    try:
        assert all(is_midi_file(session.extract_file(name)) for name in names)
    finally:
        session.cleanup()


def test_midi_images_preserve_titles_beyond_the_eseq_limit(tmp_path):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    long_title = "Mussorgsky - Bydlo (from Pictures at an Exhibition)"
    assert len(long_title) > 32
    source_path = source / "Bydlo.mid"
    source_path.write_bytes(_midi_bytes(long_title))

    result = build_emulator_disk_images(
        source,
        output,
        prefix="LONG",
        disk_format=_disk_format(),
        output_content="midi",
        output_ext="img",
        include_song_lists=True,
    )

    session = FloppyImageSession.load(result.output_paths[0])
    try:
        midi_entries = [
            entry
            for entry in session.list_entries().entries
            if entry.name.upper().endswith(".MID")
        ]
        assert len(midi_entries) == 1
        extracted_path = session.extract_file(midi_entries[0].path)
        assert Path(extracted_path).read_bytes() == source_path.read_bytes()
        assert extract_first_title_from_midi(extracted_path) == long_title
    finally:
        session.cleanup()

    assert long_title in Path(result.song_list_path).read_text(encoding="utf-8")


def test_index_csv_title_replaces_native_midi_title_without_truncation(tmp_path):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    source_path = source / "001 - Bydlo.mid"
    source_path.write_bytes(_midi_bytes("Embedded short title"))
    index_title = "Mussorgsky - Bydlo (from Pictures at an Exhibition)"
    _write_index_csv(
        source,
        [
            {
                "number": 1,
                "output_folder": ".",
                "output_file": source_path.name,
                "title": index_title,
            }
        ],
    )

    result = build_emulator_disk_images(
        source,
        output,
        prefix="INDX",
        disk_format=_disk_format(),
        output_content="midi",
        output_ext="img",
        include_song_lists=True,
    )

    session = FloppyImageSession.load(result.output_paths[0])
    try:
        midi_entry = next(
            entry
            for entry in session.list_entries().entries
            if entry.name.upper().endswith(".MID")
        )
        assert extract_first_title_from_midi(
            session.extract_file(midi_entry.path)
        ) == index_title
    finally:
        session.cleanup()

    assert index_title in Path(result.song_list_path).read_text(encoding="utf-8")


def test_index_csv_output_folder_disambiguates_duplicate_filenames(tmp_path):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    first_folder = source / "first"
    second_folder = source / "second"
    first_folder.mkdir(parents=True)
    second_folder.mkdir()
    (first_folder / "Song.mid").write_bytes(_midi_bytes("Embedded first"))
    (second_folder / "Song.mid").write_bytes(_midi_bytes("Embedded second"))
    first_title = "First indexed title"
    second_title = "Second indexed title"
    _write_index_csv(
        source,
        [
            {
                "number": 1,
                "output_folder": "first",
                "output_file": "Song.mid",
                "title": first_title,
            },
            {
                "number": 2,
                "output_folder": "second",
                "output_file": "Song.mid",
                "title": second_title,
            },
        ],
    )

    result = build_emulator_disk_images(
        source,
        output,
        prefix="PATH",
        disk_format=_disk_format(),
        output_content="midi",
        output_ext="img",
    )

    session = FloppyImageSession.load(result.output_paths[0])
    try:
        titles = {
            extract_first_title_from_midi(session.extract_file(entry.path))
            for entry in session.list_entries().entries
            if entry.name.upper().endswith(".MID")
        }
    finally:
        session.cleanup()

    assert titles == {first_title, second_title}


def test_index_csv_hash_match_restores_full_title_when_converting_eseq_to_midi(
    tmp_path,
):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    midi_path = source / "seed.mid"
    midi_path.write_bytes(_midi_bytes("Original title that reaches 32!!"))
    eseq_path = source / "LEGACY.FIL"
    convert_midi_file_to_eseq_path(midi_path, eseq_path)
    midi_path.unlink()
    index_title = "Mussorgsky - Ballet of the Unhatched Chickens (Pictures)"
    _write_index_csv(
        source,
        [
            {
                "number": 1,
                "output_folder": ".",
                "output_file": "A different filename.mid",
                "title": index_title,
                "source_path": "Archive/Another filename.fil",
                "sha256": hashlib.sha256(eseq_path.read_bytes()).hexdigest(),
            }
        ],
    )

    result = build_emulator_disk_images(
        source,
        output,
        prefix="HASH",
        disk_format=_disk_format(),
        output_content="midi",
        output_ext="img",
        include_song_lists=True,
    )

    session = FloppyImageSession.load(result.output_paths[0])
    try:
        midi_entry = next(
            entry
            for entry in session.list_entries().entries
            if entry.name.upper().endswith(".MID")
        )
        assert extract_first_title_from_midi(
            session.extract_file(midi_entry.path)
        ) == index_title
    finally:
        session.cleanup()

    assert index_title in Path(result.song_list_path).read_text(encoding="utf-8")


def test_eseq_images_and_song_lists_use_the_on_disk_32_byte_title(tmp_path):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    source_path = source / "Bydlo.mid"
    source_path.write_bytes(_midi_bytes("Embedded short title"))
    index_title = "Mussorgsky - Bydlo (from Pictures at an Exhibition)"
    expected_title = index_title[:32]
    _write_index_csv(
        source,
        [
            {
                "number": 1,
                "output_folder": ".",
                "output_file": source_path.name,
                "title": index_title,
            }
        ],
    )

    result = build_emulator_disk_images(
        source,
        output,
        prefix="ESEQ",
        disk_format=_disk_format(),
        output_content="eseq",
        output_ext="img",
        include_song_lists=True,
    )

    session = FloppyImageSession.load(result.output_paths[0])
    try:
        eseq_entries = [
            entry
            for entry in session.list_entries().entries
            if entry.name.upper().endswith(".FIL")
            and entry.name.upper() != PIANODIR_FILENAME
        ]
        assert len(eseq_entries) == 1
        extracted_path = session.extract_file(eseq_entries[0].path)
        assert extract_eseq_title_from_file(extracted_path) == expected_title
    finally:
        session.cleanup()

    song_list = Path(result.song_list_path).read_text(encoding="utf-8")
    assert f"1. {expected_title}\n" in song_list
    assert index_title not in song_list


def test_index_csv_updates_an_existing_eseq_title_with_the_32_byte_limit(
    tmp_path,
):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    seed_path = tmp_path / "seed.mid"
    seed_path.write_bytes(_midi_bytes("Embedded E-SEQ title"))
    eseq_path = source / "LEGACY.FIL"
    convert_midi_file_to_eseq_path(seed_path, eseq_path)
    index_title = "Rubinstein - Romance in E flat major, complete title"
    expected_title = index_title[:32]
    _write_index_csv(
        source,
        [
            {
                "number": 1,
                "output_folder": ".",
                "output_file": eseq_path.name,
                "title": index_title,
            }
        ],
    )

    result = build_emulator_disk_images(
        source,
        output,
        prefix="COPY",
        disk_format=_disk_format(),
        output_content="eseq",
        output_ext="img",
        include_song_lists=True,
    )

    session = FloppyImageSession.load(result.output_paths[0])
    try:
        eseq_entry = next(
            entry
            for entry in session.list_entries().entries
            if entry.name.upper().endswith(".FIL")
            and entry.name.upper() != PIANODIR_FILENAME
        )
        assert extract_eseq_title_from_file(
            session.extract_file(eseq_entry.path)
        ) == expected_title
    finally:
        session.cleanup()

    song_list = Path(result.song_list_path).read_text(encoding="utf-8")
    assert f"1. {expected_title}\n" in song_list
    assert index_title not in song_list


def test_builds_eseq_only_images_without_including_source_midi(tmp_path):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    midi_path = source / "Needs Conversion.mid"
    midi_path.write_bytes(_midi_bytes("Needs Conversion"))
    eseq_path = source / "Already ESEQ.fil"
    convert_midi_file_to_eseq_path(
        midi_path,
        eseq_path,
        filename_hint="ALREADYE.FIL",
    )

    result = build_emulator_disk_images(
        source,
        output,
        set_name="ESEQ Set",
        disk_format=_disk_format(),
        output_content="eseq",
        output_ext="img",
    )

    assert result.files_prepared == 2
    assert result.converted_files == 1
    listing = read_image_listing(result.output_paths[0])
    names = [entry.name for entry in listing.entries]
    assert names.count(PIANODIR_FILENAME) == 1
    assert len(names) == 3
    assert not any(name.upper().endswith(".MID") for name in names)
    assert all(
        name == PIANODIR_FILENAME or name.upper().endswith(".FIL")
        for name in names
    )


def test_existing_output_is_not_overwritten(tmp_path):
    source = tmp_path / "midi"
    output = tmp_path / "images"
    source.mkdir()
    (source / "Song.mid").write_bytes(_midi_bytes("Song"))
    output.mkdir()
    existing = output / "EXIS0000.img"
    existing.write_bytes(b"keep this")
    before = hashlib.sha256(existing.read_bytes()).digest()

    with pytest.raises(FloppyImageError, match="already exists"):
        build_emulator_eseq_images(
            source,
            output,
            set_name="Existing",
            disk_format=_disk_format(),
            output_ext="img",
        )

    assert hashlib.sha256(existing.read_bytes()).digest() == before


def test_existing_output_can_be_approved_for_replacement(tmp_path):
    source = tmp_path / "midi"
    output = tmp_path / "images"
    source.mkdir()
    output.mkdir()
    (source / "Song.mid").write_bytes(_midi_bytes("Song"))
    existing = output / "REPL0007.img"
    existing.write_bytes(b"previous image")
    requested_paths = []

    result = build_emulator_disk_images(
        source,
        output,
        prefix="REPL",
        starting_number=7,
        disk_format=_disk_format(),
        output_ext="img",
        output_content="midi",
        overwrite_callback=lambda paths: requested_paths.extend(paths) or True,
    )

    assert requested_paths == [str(existing)]
    assert result.output_paths == (str(existing),)
    assert existing.read_bytes() != b"previous image"
    assert len(read_image_listing(existing).entries) == 1


def test_declining_output_replacement_preserves_existing_file(tmp_path):
    source = tmp_path / "midi"
    output = tmp_path / "images"
    source.mkdir()
    output.mkdir()
    (source / "Song.mid").write_bytes(_midi_bytes("Song"))
    existing = output / "STAY0003.img"
    existing.write_bytes(b"keep this image")

    with pytest.raises(FloppyOperationCancelled, match="replacement was cancelled"):
        build_emulator_disk_images(
            source,
            output,
            prefix="STAY",
            starting_number=3,
            disk_format=_disk_format(),
            output_ext="img",
            output_content="midi",
            overwrite_callback=lambda _paths: False,
        )

    assert existing.read_bytes() == b"keep this image"


def test_replacement_commit_failure_restores_every_existing_image(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "midi"
    output = tmp_path / "images"
    source.mkdir()
    output.mkdir()
    for index in range(1, 3):
        (source / f"Song {index}.mid").write_bytes(
            _midi_bytes(f"Song {index}")
        )
    first = output / "ROLL0001.img"
    second = output / "ROLL0002.img"
    first.write_bytes(b"first existing image")
    second.write_bytes(b"second existing image")
    real_finish = emulator_image_builder._finish_temp_output
    commit_calls = []

    def fail_second_commit(staged_path, final_path):
        commit_calls.append(final_path)
        if len(commit_calls) == 2:
            raise OSError("simulated commit failure")
        return real_finish(staged_path, final_path)

    monkeypatch.setattr(emulator_image_builder, "PIANODIR_MAX_TRACKS", 1)
    monkeypatch.setattr(
        emulator_image_builder,
        "_finish_temp_output",
        fail_second_commit,
    )

    with pytest.raises(OSError, match="simulated commit failure"):
        build_emulator_disk_images(
            source,
            output,
            prefix="ROLL",
            starting_number=1,
            disk_format=_disk_format(),
            output_ext="img",
            output_content="eseq",
            overwrite_existing=True,
        )

    assert commit_calls == [str(first), str(second)]
    assert first.read_bytes() == b"first existing image"
    assert second.read_bytes() == b"second existing image"


def test_default_prefix_numbers_even_a_single_image(tmp_path):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    (source / "Song.mid").write_bytes(_midi_bytes("Song"))

    result = build_emulator_disk_images(
        source,
        output,
        disk_format=_disk_format(),
        output_ext="img",
        output_content="midi",
    )

    assert result.starting_number == 0
    assert [Path(path).name for path in result.output_paths] == ["DSKA0000.img"]
    assert result.song_list_path == ""
    assert not list(output.glob("*.txt"))


def test_include_song_lists_writes_every_image_and_song_in_packed_order(
    tmp_path,
    monkeypatch,
):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    for index, title in enumerate(("First   Song", "Second Song", "Third Song"), start=1):
        (source / f"Song {index}.mid").write_bytes(_midi_bytes(title))

    monkeypatch.setattr(emulator_image_builder, "PIANODIR_MAX_TRACKS", 2)
    result = build_emulator_disk_images(
        source,
        output,
        prefix="LIST",
        starting_number=5,
        album_title="Customer Album",
        disk_format=_disk_format(),
        output_ext="img",
        output_content="eseq",
        include_song_lists=True,
    )

    assert Path(result.song_list_path).name == "LIST0005-LIST0006-song-lists.txt"
    assert Path(result.song_list_path).read_text(encoding="utf-8") == (
        "Emulator Disk Set Song Lists\n"
        "Images: 2\n"
        "Songs: 3\n"
        "\n"
        "Image 1 of 2: LIST0005.img\n"
        "Album: Customer Album\n"
        "Catalog: LIST-0005\n"
        "\n"
        "Source album: songs\n"
        "1. First Song\n"
        "2. Second Song\n"
        "\n"
        "Image 2 of 2: LIST0006.img\n"
        "Album: Customer Album\n"
        "Catalog: LIST-0006\n"
        "\n"
        "Source album: songs\n"
        "1. Third Song\n"
    )


def test_existing_song_list_is_not_overwritten(tmp_path):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    output.mkdir()
    (source / "Song.mid").write_bytes(_midi_bytes("Song"))
    existing = output / "KEEP0007-song-list.txt"
    existing.write_text("keep this", encoding="utf-8")

    with pytest.raises(FloppyImageError, match="already exists"):
        build_emulator_disk_images(
            source,
            output,
            prefix="KEEP",
            starting_number=7,
            disk_format=_disk_format(),
            output_ext="img",
            output_content="midi",
            include_song_lists=True,
        )

    assert existing.read_text(encoding="utf-8") == "keep this"
    assert not (output / "KEEP0007.img").exists()


def test_eseq_album_title_defaults_to_the_generated_catalog_id(tmp_path):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    (source / "Song.mid").write_bytes(_midi_bytes("Song"))

    result = build_emulator_disk_images(
        source,
        output,
        prefix="DSKC",
        starting_number=42,
        disk_format=_disk_format(),
        output_ext="img",
        output_content="eseq",
    )

    session = FloppyImageSession.load(result.output_paths[0])
    try:
        metadata = read_pianodir_metadata_from_file(
            session.extract_file(PIANODIR_FILENAME)
        )
    finally:
        session.cleanup()

    assert metadata.catalog_number == "DSKC-0042"
    assert metadata.disk_title == "DSKC-0042"


def test_shuffle_randomizes_the_order_before_disk_packing(tmp_path, monkeypatch):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    for index in range(1, 4):
        (source / f"Song {index}.mid").write_bytes(_midi_bytes(f"Song {index}"))

    monkeypatch.setattr(
        emulator_image_builder.random,
        "shuffle",
        lambda paths: paths.reverse(),
    )
    result = build_emulator_disk_images(
        source,
        output,
        shuffle=True,
        disk_format=_disk_format(),
        output_ext="img",
        output_content="midi",
        include_song_lists=True,
    )

    session = FloppyImageSession.load(result.output_paths[0])
    try:
        titles = [
            extract_first_title_from_midi(session.extract_file(entry.path))
            for entry in session.list_entries().entries
        ]
    finally:
        session.cleanup()

    assert result.shuffled is True
    assert titles == ["Song 3", "Song 2", "Song 1"]
    song_list_text = Path(result.song_list_path).read_text(encoding="utf-8")
    assert song_list_text.index("1. Song 3") < song_list_text.index("2. Song 2")
    assert song_list_text.index("2. Song 2") < song_list_text.index("3. Song 1")


def test_reserves_requested_free_space_when_packing_disks(tmp_path):
    source = tmp_path / "songs"
    output = tmp_path / "images"
    source.mkdir()
    for index in range(1, 3):
        (source / f"Large {index}.mid").write_bytes(
            _large_midi_bytes(f"Large {index}", 190_000)
        )

    margin = 400 * 1024
    result = build_emulator_disk_images(
        source,
        output,
        prefix="SAFE",
        starting_number=7,
        safety_margin_bytes=margin,
        disk_format=_disk_format(),
        output_ext="img",
        output_content="midi",
    )

    assert [Path(path).name for path in result.output_paths] == [
        "SAFE0007.img",
        "SAFE0008.img",
    ]
    assert all(
        read_image_listing(path).free_space >= margin
        for path in result.output_paths
    )


@pytest.mark.parametrize("starting_number", [-1, 10_000, "invalid"])
def test_rejects_invalid_starting_numbers(tmp_path, starting_number):
    source = tmp_path / "songs"
    source.mkdir()
    (source / "Song.mid").write_bytes(_midi_bytes("Song"))

    with pytest.raises(FloppyImageError, match="starting disk number"):
        build_emulator_disk_images(
            source,
            tmp_path / "images",
            starting_number=starting_number,
            output_ext="img",
            output_content="midi",
        )


def test_rejects_a_set_that_would_run_past_slot_9999(tmp_path, monkeypatch):
    source = tmp_path / "songs"
    source.mkdir()
    for index in range(2):
        (source / f"Song {index}.mid").write_bytes(_midi_bytes(f"Song {index}"))
    monkeypatch.setattr(emulator_image_builder, "PIANODIR_MAX_TRACKS", 1)

    with pytest.raises(FloppyImageError, match="exceed disk 9999"):
        build_emulator_disk_images(
            source,
            tmp_path / "images",
            starting_number=9999,
            output_ext="img",
            output_content="eseq",
        )


def _image_songs_and_metadata(image_path, output_content):
    session = FloppyImageSession.load(image_path)
    try:
        listing = session.list_entries()
        songs = []
        for entry in listing.entries:
            if entry.name in {PIANODIR_FILENAME, "PSONG.MNG", "PDISK.MNG"}:
                continue
            path = session.extract_file(entry.path)
            songs.append(
                extract_first_title_from_midi(path)
                if output_content == "midi" else extract_eseq_title_from_file(path)
            )
        metadata = (
            read_pianodir_metadata_from_file(session.extract_file(PIANODIR_FILENAME))
            if output_content == "eseq" else None
        )
        return songs, metadata
    finally:
        session.cleanup()


@pytest.mark.parametrize("output_content", ["midi", "eseq"])
@pytest.mark.parametrize("output_ext", ["img", "hfe"])
def test_folder_albums_keep_root_nested_and_sibling_songs_separate(
    tmp_path, output_content, output_ext,
):
    source = tmp_path / "Collection"
    for relative in ["DSKA010", "DSKA002/Bonus", "Empty"]:
        (source / relative).mkdir(parents=True)
    (source / "Root.mid").write_bytes(_midi_bytes("Root song"))
    for relative, title in [
        ("DSKA002/Song 10.mid", "Second album ten"),
        ("DSKA002/Song 2.mid", "Second album two"),
        ("DSKA010/Song 2.mid", "Tenth album"),
        ("DSKA002/Bonus/Song 2.mid", "Bonus song"),
    ]:
        (source / relative).write_bytes(_midi_bytes(title))
    eseq_path = source / "DSKA010" / "Song 2.fil"
    convert_midi_file_to_eseq_path(source / "DSKA010" / "Song 2.mid", eseq_path)
    (source / "DSKA010" / "Song 2.mid").unlink()
    (source / "Empty" / "PIANODIR.FIL").write_bytes(b"not a song")

    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders",
        output_content=output_content, output_ext=output_ext,
        prefix="DSKB", starting_number=12, include_song_lists=True,
    )

    assert result.disk_layout == "folders"
    assert result.song_files_found == result.files_prepared == 5
    assert result.images_created == 4
    assert [Path(path).name for path in result.output_paths] == [
        f"DSKB{index:04d}.{output_ext}" for index in range(12, 16)
    ]
    expected = [
        (["Root song"], "Collection"),
        (["Second album two", "Second album ten"], "DSKA002"),
        (["Tenth album"], "DSKA010"),
        (["Bonus song"], "Bonus"),
    ]
    for index, (path, (titles, album)) in enumerate(zip(result.output_paths, expected), 12):
        songs, metadata = _image_songs_and_metadata(path, output_content)
        assert songs == titles
        if metadata is not None:
            assert metadata.disk_title == album
            assert metadata.catalog_number == f"DSKB-{index:04d}"
    song_list = Path(result.song_list_path).read_text(encoding="utf-8")
    assert "Folder: Collection" in song_list
    assert "Folder: DSKA002/Bonus" in song_list
    assert "Album: DSKA002" in song_list
    sections = song_list.split("\nImage ")[1:]
    assert len(sections) == result.images_created
    for index, (section, (titles, album)) in enumerate(zip(sections, expected), 12):
        assert f"DSKB{index:04d}.{output_ext}" in section
        assert f"Album: {album}\n" in section
        assert [line for line in section.splitlines() if line.split(". ", 1)[0].isdigit()] == [
            f"{track}. {title}" for track, title in enumerate(titles, 1)
        ]
    assert list((tmp_path / "images").glob("*.txt")) == [Path(result.song_list_path)]


@pytest.mark.parametrize("disk_layout", ["folders", "fill"])
def test_only_fill_layout_allows_nonrecursive_selected_folder_scope(tmp_path, disk_layout):
    source = tmp_path / "songs"
    child = source / "child"
    child.mkdir(parents=True)
    grandchild = child / "nested"
    grandchild.mkdir()
    (source / "Root.mid").write_bytes(_midi_bytes("Root"))
    (child / "Child.mid").write_bytes(_midi_bytes("Child"))
    (grandchild / "Nested.mid").write_bytes(_midi_bytes("Nested"))
    result = build_emulator_disk_images(
        source, tmp_path / "images", output_ext="img", output_content="midi",
        disk_layout=disk_layout, include_subfolders=False,
    )
    expected = [["Root"], ["Child"], ["Nested"]] if disk_layout == "folders" else [["Root"]]
    assert result.song_files_found == len(expected)
    assert result.images_created == len(expected)
    assert [_image_songs_and_metadata(path, "midi")[0] for path in result.output_paths] == expected


def test_fill_layout_still_combines_folders_into_one_disk(tmp_path):
    source = tmp_path / "songs"
    for folder in ["Album 2", "Album 10"]:
        (source / folder).mkdir(parents=True)
        (source / folder / "Song.mid").write_bytes(_midi_bytes(folder))
    result = build_emulator_disk_images(
        source, tmp_path / "images", output_ext="img", output_content="midi",
        disk_layout="fill",
    )
    assert result.images_created == 1
    assert _image_songs_and_metadata(result.output_paths[0], "midi")[0] == [
        "Album 2", "Album 10",
    ]


@pytest.mark.parametrize("output_content", ["midi", "eseq"])
def test_oversized_folder_spills_without_filling_remainder_from_next_album(
    tmp_path, monkeypatch, output_content,
):
    source = tmp_path / "songs"
    for folder in ["Album 1", "Album 2"]:
        (source / folder).mkdir(parents=True)
    for index in range(1, 4):
        payload = (
            _large_midi_bytes(f"Song {index}", 250_000)
            if output_content == "midi" else _midi_bytes(f"Song {index}")
        )
        (source / "Album 1" / f"Song {index}.mid").write_bytes(payload)
    (source / "Album 2" / "Last.mid").write_bytes(_midi_bytes("Last album"))
    monkeypatch.setattr(emulator_image_builder, "PIANODIR_MAX_TRACKS", 2)
    result = build_emulator_disk_images(
        source, tmp_path / "images", output_ext="img", output_content=output_content,
        disk_layout="folders", include_song_lists=True,
    )
    assert result.images_created == 3
    images = [_image_songs_and_metadata(path, output_content) for path in result.output_paths]
    assert [songs for songs, metadata in images] == [
        ["Song 1", "Song 2"], ["Song 3"], ["Last album"],
    ]
    if output_content == "eseq":
        assert [metadata.disk_title for songs, metadata in images] == [
            "Album 1", "Album 1", "Album 2",
        ]
    for path in result.output_paths:
        assert read_image_listing(path).free_space >= DEFAULT_SAFETY_MARGIN_BYTES
    text = Path(result.song_list_path).read_text(encoding="utf-8")
    sections = text.split("\nImage ")[1:]
    for index, (section, (titles, _metadata), album) in enumerate(zip(
        sections, images, ["Album 1", "Album 1", "Album 2"],
    )):
        assert f"DSKA{index:04d}.img" in section
        assert f"Album: {album}\n" in section
        assert "\n".join(f"{track}. {title}" for track, title in enumerate(titles, 1)) in section
    assert len(sections) == 3
    assert list((tmp_path / "images").glob("*.txt")) == [Path(result.song_list_path)]


def test_folder_shuffle_and_local_index_titles_preserve_album_boundaries(tmp_path, monkeypatch):
    source = tmp_path / "songs"
    source.mkdir()
    _write_index_csv(source, [
        {"output_folder": "Album 2", "output_file": "Song 1.mid", "title": "Root title"},
        {"output_folder": "Album 1", "output_file": "Song 1.mid", "title": "Superseded"},
    ])
    for folder in ["Album 1", "Album 2"]:
        album = source / folder
        album.mkdir()
        for index in [1, 2]:
            (album / f"Song {index}.mid").write_bytes(_midi_bytes(f"{folder} song {index}"))
    _write_index_csv(source / "Album 1", [
        {"output_file": "Song 1.mid", "title": "Local title"},
        {"output_file": "Song 2.mid", "title": ""},
    ])
    monkeypatch.setattr(emulator_image_builder.random, "shuffle", lambda paths: paths.reverse())
    result = build_emulator_disk_images(
        source, tmp_path / "images", output_ext="img", disk_layout="folders",
        shuffle=True, album_title="Shared album", include_song_lists=True,
    )
    assert result.shuffled
    images = [_image_songs_and_metadata(path, "eseq") for path in result.output_paths]
    assert [songs for songs, metadata in images] == [
        ["Album 1 song 2", "Local title"], ["Album 2 song 2", "Root title"],
    ]
    assert [metadata.disk_title for songs, metadata in images] == ["Shared album"] * 2
    text = Path(result.song_list_path).read_text(encoding="utf-8")
    assert text.index("Folder: Album 1") < text.index("Folder: Album 2")
    assert text.index("Album 1 song 2") < text.index("Local title")


def test_folder_build_slot_overflow_does_not_commit_any_images(tmp_path):
    source = tmp_path / "songs"
    for folder in ["Album 1", "Album 2"]:
        (source / folder).mkdir(parents=True)
        (source / folder / "Song.mid").write_bytes(_midi_bytes(folder))
    output = tmp_path / "images"
    with pytest.raises(FloppyImageError, match="exceed disk 9999"):
        build_emulator_disk_images(
            source, output, disk_layout="folders", output_ext="img", starting_number=9999,
        )
    assert list(output.iterdir()) == []


def test_folder_build_rejects_empty_collection_and_unknown_layout(tmp_path):
    source = tmp_path / "songs"
    (source / "empty").mkdir(parents=True)
    output = tmp_path / "images"
    with pytest.raises(FloppyImageError, match="does not contain any"):
        build_emulator_disk_images(source, output, disk_layout="folders")
    with pytest.raises(FloppyImageError, match="Disk layout"):
        build_emulator_disk_images(source, output, disk_layout="unknown")
    assert not output.exists()


@pytest.mark.parametrize("output_content", ["midi", "eseq"])
def test_recursive_fill_song_list_keeps_source_albums_in_shuffled_image_order(
    tmp_path, monkeypatch, output_content,
):
    source = tmp_path / "Collection"
    album_a = source / "Album 1"
    album_b = source / "Nested" / "Album 2"
    album_a.mkdir(parents=True)
    album_b.mkdir(parents=True)
    (album_a / "Track 1.mid").write_bytes(_midi_bytes("First title"))
    (album_a / "Track 2.mid").write_bytes(_midi_bytes("Second title"))
    (album_b / "Track 1.mid").write_bytes(_midi_bytes("Nested title"))

    def interleave(paths):
        paths[:] = [paths[0], paths[2], paths[1]]

    monkeypatch.setattr(emulator_image_builder.random, "shuffle", interleave)
    monkeypatch.setattr(emulator_image_builder, "PIANODIR_MAX_TRACKS", 2)
    output = tmp_path / "images"
    result = build_emulator_disk_images(
        source, output, disk_layout="fill", include_subfolders=True,
        output_ext="img", output_content=output_content, shuffle=True,
        include_song_lists=True,
    )
    assert result.images_created == (2 if output_content == "eseq" else 1)
    text = Path(result.song_list_path).read_text(encoding="utf-8")
    assert "Songs: 3\n" in text
    sections = text.split("\nImage ")[1:]
    assert len(sections) == result.images_created
    for section, image_path in zip(sections, result.output_paths):
        titles, _metadata = _image_songs_and_metadata(image_path, output_content)
        assert Path(image_path).name in section
        assert [line for line in section.splitlines() if line.split(". ", 1)[0].isdigit()] == [
            f"{track}. {title}" for track, title in enumerate(titles, 1)
        ]
    assert text.count("Source album: Album 1\n") == 2
    assert "Source album: Nested/Album 2\n2. Nested title\n" in text
    assert text.index("First title") < text.index("Nested title") < text.index("Second title")
    assert list(output.glob("*.txt")) == [Path(result.song_list_path)]


def test_combined_song_list_falls_back_to_filenames_for_missing_or_blank_titles(tmp_path):
    source = tmp_path / "Collection"
    for folder, filename, title in [
        ("Album 1", "No title.mid", ""),
        ("Album 2", "Blank title.mid", "   "),
    ]:
        album = source / folder
        album.mkdir(parents=True)
        (album / filename).write_bytes(_midi_bytes(title))
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_ext="img",
        output_content="midi", include_song_lists=True,
    )
    text = Path(result.song_list_path).read_text(encoding="utf-8")
    assert "Image 1 of 2: DSKA0000.img\nFolder: Album 1\nAlbum: Album 1\n\n1. No title\n" in text
    assert "Image 2 of 2: DSKA0001.img\nFolder: Album 2\nAlbum: Album 2\n\n1. Blank title\n" in text


@pytest.mark.parametrize("disk_layout", ["folders", "fill"])
def test_disabling_song_lists_applies_to_entire_recursive_build(tmp_path, disk_layout):
    source = tmp_path / "Collection"
    for folder in ["Album 1", "Nested/Album 2"]:
        album = source / folder
        album.mkdir(parents=True)
        (album / "Track.mid").write_bytes(_midi_bytes("Song title"))
    output = tmp_path / "images"
    result = build_emulator_disk_images(
        source, output, disk_layout=disk_layout, include_subfolders=True,
        output_ext="img", output_content="midi", include_song_lists=False,
    )
    assert result.song_files_found == 2
    assert result.images_created == (2 if disk_layout == "folders" else 1)
    assert result.song_list_path == ""
    assert set(output.iterdir()) == {Path(path) for path in result.output_paths}


def _write_mng_catalogs(folder, album, songs):
    pdisk = bytearray(b" " * 128)
    pdisk[:16] = b"PDISK   MNG   \r\n"
    pdisk[16:32] = b"P.PLAYER      \r\n"
    pdisk[32:48] = b"Ver1.01DMV0.53\r\n"
    pdisk[48:112] = album.encode("cp1252").ljust(64, b" ")
    pdisk[112:114] = b"\r\n"
    (folder / "PDISK.MNG").write_bytes(pdisk)
    psong = bytearray(b" " * (0x80 + len(songs) * 0xB0))
    psong[:16] = b"PSONG   MNG   \r\n"
    psong[0x20:0x30] = f"FILE{len(songs):03d}       \r\n".encode("ascii")
    for index, (filename, title) in enumerate(songs):
        start = 0x80 + index * 0xB0
        stem, extension = filename.rsplit(".", 1)
        psong[start:start + 11] = stem.encode("cp1252").ljust(8, b" ") + extension.encode("cp1252").ljust(3, b" ")
        psong[start + 16:start + 48] = title.encode("cp1252").ljust(32, b" ")
    (folder / "PSONG.MNG").write_bytes(psong)


def _image_files(image_path):
    session = FloppyImageSession.load(image_path)
    try:
        return {
            entry.name: Path(session.extract_file(entry.path)).read_bytes()
            for entry in session.list_entries().entries
        }
    finally:
        session.cleanup()


@pytest.mark.parametrize("damaged_name, content", (
    ("song", "midi"), ("PSONG.MNG", "midi"), ("PIANODIR.FIL", "eseq"),
))
def test_packing_corruption_is_rejected_before_preview_or_delivery(tmp_path, monkeypatch, damaged_name, content):
    source = tmp_path / "packing-source"
    source.mkdir()
    (source / "SONG.MID").write_bytes(_midi_bytes("Packing verification"))
    _write_mng_catalogs(source, "Packing album", [("SONG.MID", "Packing verification")])
    output = tmp_path / "output"
    output.mkdir()
    existing = output / "DSKA0000.img"
    existing.write_bytes(b"previous delivery")
    real_copy = emulator_image_builder._copy_host_file_into_image
    damaged = []
    previews = []

    def corrupt_after_copy(raw_path, host_path, image_path, **kwargs):
        result = real_copy(raw_path, host_path, image_path, **kwargs)
        matches = image_path.upper().endswith(".MID") if damaged_name == "song" else image_path.upper() == damaged_name
        payload = Path(host_path).read_bytes()
        # Skip the empty PIANODIR placeholder and corrupt its completed catalog.
        if matches and damaged_name == "PIANODIR.FIL":
            matches = int.from_bytes(payload[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2], "little") > 1
        if matches and not damaged:
            raw = bytearray(Path(raw_path).read_bytes())
            position = raw.index(payload)
            raw[position + len(payload) // 2] ^= 1
            Path(raw_path).write_bytes(raw)
            damaged.append(image_path)
        return result

    monkeypatch.setattr(emulator_image_builder, "_copy_host_file_into_image", corrupt_after_copy)
    with pytest.raises(FloppyImageError, match="packed contents differ"):
        build_emulator_disk_images(
            source, output, output_content=content, output_ext="img", overwrite_existing=True,
            review_callback=lambda preview: previews.append(preview) or {"action": "build"},
        )
    assert len(damaged) == 1
    assert previews == []
    assert existing.read_bytes() == b"previous delivery"
    assert list(output.iterdir()) == [existing]
    assert (source / "SONG.MID").read_bytes() == _midi_bytes("Packing verification")


@pytest.mark.parametrize("content", ("midi", "eseq"))
def test_generated_catalog_must_reference_prepared_songs_in_playback_order(tmp_path, monkeypatch, content):
    source = tmp_path / "source"
    source.mkdir()
    for name in ("FIRST", "SECOND"):
        (source / f"{name}.MID").write_bytes(_midi_bytes(name))
    _write_mng_catalogs(source, "Ordered album", [("FIRST.MID", "FIRST"), ("SECOND.MID", "SECOND")])
    if content == "midi":
        real_builder = emulator_image_builder.build_smart_pianosoft_song_catalog
        monkeypatch.setattr(emulator_image_builder, "build_smart_pianosoft_song_catalog", lambda template, records: real_builder(template, list(reversed(records))))
    else:
        real_builder = emulator_image_builder.build_pianodir_bytes
        monkeypatch.setattr(emulator_image_builder, "build_pianodir_bytes", lambda tracks, **kwargs: real_builder(list(reversed(tracks)), **kwargs))
    with pytest.raises(FloppyImageError, match="song references or order differ"):
        build_emulator_disk_images(source, tmp_path / "output", output_content=content, output_ext="img")
    assert list((tmp_path / "output").iterdir()) == []


@pytest.mark.parametrize("output_content", ["midi", "eseq"])
@pytest.mark.parametrize("output_ext", ["img", "hfe"])
def test_folder_mng_titles_follow_original_renamed_and_converted_songs(
    tmp_path, output_content, output_ext,
):
    source = tmp_path / "Collection"
    first = source / "DSKA001"
    second = source / "Nested" / "DSKA002"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    _write_mng_catalogs(first, "Evening Jazz", [
        ("FIRST.MID", "Moon River"), ("SECOND.MID", "Summer Wind"),
        ("THIRD.MID", "Night and Day"),
    ])
    _write_mng_catalogs(second, "Sunday Classics", [("FIRST.MID", "Morning Prelude")])
    # Catalog lookup is local and case-insensitive, and extraction may have
    # already replaced an original DOS name with its numbered title.
    (first / "first.MiD").write_bytes(_midi_bytes("Old first title"))
    (first / "02 - Summer Wind.mid").write_bytes(_midi_bytes("Old second title"))
    (second / "FIRST.MID").write_bytes(_midi_bytes("Old other album"))
    convert_midi_file_to_eseq_path(first / "first.MiD", first / "THIRD")
    (first / "NOTES.FIL").write_bytes(b"Not an E-SEQ song.")
    (second / "PSONG.MNG").rename(second / "psong.mng")
    (second / "PDISK.MNG").rename(second / "pdisk.mng")
    originals = {path: path.read_bytes() for path in source.rglob("*") if path.is_file()}
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_content=output_content,
        output_ext=output_ext, include_song_lists=True,
    )
    assert result.images_created == 2
    expected = [
        (["Summer Wind", "Moon River", "Night and Day"], "Evening Jazz"),
        (["Morning Prelude"], "Sunday Classics"),
    ]
    text = Path(result.song_list_path).read_text(encoding="utf-8")
    sections = text.split("\nImage ")[1:]
    for image_path, section, (titles, album), folder in zip(
        result.output_paths, sections, expected, [first, second],
    ):
        image_titles, metadata = _image_songs_and_metadata(image_path, output_content)
        assert image_titles == titles
        assert f"Album: {album}\n" in section
        if metadata is not None:
            assert metadata.disk_title == album
        assert all(title in section for title in titles)
        files = _image_files(image_path)
        if output_content == "midi":
            catalog = parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])
            assert [song.filename for song in catalog] == [name for name in files if name.endswith(".MID")]
            assert [song.title for song in catalog] == titles
            original_disk = next(path for path in originals if path.parent == folder and path.name.upper() == "PDISK.MNG")
            assert files["PDISK.MNG"] == originals[original_disk]
            assert PIANODIR_FILENAME not in files
        else:
            assert "PSONG.MNG" not in files
            assert "PDISK.MNG" not in files
    assert {path: path.read_bytes() for path in originals} == originals


@pytest.mark.parametrize("output_content", ["midi", "eseq"])
def test_extracted_catalog_names_override_numeric_embedded_titles_in_hfe_sets(
    tmp_path, monkeypatch, output_content,
):
    source = tmp_path / "Extracted Images"
    album = source / "Artist - Folder album"
    album.mkdir(parents=True)
    records = [
        ("01.mid", "First catalog title", "01"),
        ("02.MID", "Second catalog title", "2"),
        ("03THIR-1.mid", "Third catalog title", "03 Embedded title"),
    ]
    _write_mng_catalogs(album, "Catalog album", [
        (filename, title) for filename, title, embedded in records
    ])
    for index, (filename, title, embedded) in enumerate(records, start=1):
        (album / f"{index:02d} - {title}.mid").write_bytes(_midi_bytes(embedded))
    monkeypatch.setattr(emulator_image_builder.random, "shuffle", lambda paths: paths.reverse())

    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_content=output_content,
        output_ext="hfe", include_song_lists=True, shuffle=True, starting_number=0,
    )

    expected = [title for filename, title, embedded in reversed(records)]
    assert result.images_created == 1
    assert _image_songs_and_metadata(result.output_paths[0], output_content)[0] == expected
    report = Path(result.song_list_path).read_text(encoding="utf-8")
    assert "Image 1 of 1: DSKA0000.hfe\n" in report
    assert "Album: Catalog album\n" in report
    assert "\n".join(f"{index}. {title}" for index, title in enumerate(expected, start=1)) in report
    assert all(f". {embedded}\n" not in report for filename, title, embedded in records)


@pytest.mark.parametrize("disk_layout", ["folders", "fill"])
def test_mng_metadata_respects_explicit_index_and_album_overrides(tmp_path, disk_layout):
    source = tmp_path / "Collection"
    album = source / "Album 1"
    album.mkdir(parents=True)
    _write_mng_catalogs(source, "Parent catalog", [("FIRST.MID", "Wrong parent title")])
    _write_mng_catalogs(album, "Catalog album", [
        ("FIRST.MID", "Catalog first"), ("SECOND.MID", "Catalog second"),
        ("THIRD.MID", ""), ("FOURTH.MID", "Catalog fourth"),
    ])
    for filename in ["FIRST.MID", "SECOND.MID", "THIRD.MID", "FOURTH.MID"]:
        (album / filename).write_bytes(_midi_bytes("Embedded title"))
    _write_index_csv(source, [
        {"output_folder": "Album 1", "output_file": "FIRST.MID", "title": "Root first"},
        {"output_folder": "Album 1", "output_file": "FOURTH.MID", "title": "Root fourth"},
    ])
    _write_index_csv(album, [
        {"output_file": "FIRST.MID", "title": "Local first"},
        {"output_file": "SECOND.MID", "title": ""},
    ])
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout=disk_layout, output_ext="img",
        album_title="Shared override", include_song_lists=True,
    )
    titles, metadata = _image_songs_and_metadata(result.output_paths[0], "eseq")
    assert titles == ["Local first", "Root fourth", "Catalog second", "Embedded title"]
    assert metadata.disk_title == "Shared override"
    text = Path(result.song_list_path).read_text(encoding="utf-8")
    assert "Album: Shared override\n" in text
    assert "Wrong parent title" not in text
    if disk_layout == "fill":
        assert "Source album: Catalog album (Album 1)\n" in text


def test_invalid_and_missing_mng_files_fall_back_independently(tmp_path):
    source = tmp_path / "Collection"
    for folder in ["Bad song catalog", "Bad disk catalog", "No catalogs"]:
        album = source / folder
        album.mkdir(parents=True)
        (album / "FIRST.MID").write_bytes(_midi_bytes("Embedded title"))
        if folder != "No catalogs":
            _write_mng_catalogs(album, "Catalog album", [("FIRST.MID", "Catalog song")])
    (source / "Bad song catalog" / "PSONG.MNG").write_bytes(b"truncated")
    (source / "Bad disk catalog" / "PDISK.MNG").write_bytes(b"truncated")
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_content="midi",
        output_ext="img", include_song_lists=True,
    )
    sections = Path(result.song_list_path).read_text(encoding="utf-8").split("\nImage ")[1:]
    assert "Album: Bad disk catalog\n\n1. Catalog song\n" in sections[0]
    assert "Album: Catalog album\n\n1. Embedded title\n" in sections[1]
    assert "Album: No catalogs\n\n1. Embedded title\n" in sections[2]
    expected_catalogs = [{"PSONG.MNG"}, {"PDISK.MNG"}, set()]
    for path, catalogs in zip(result.output_paths, expected_catalogs):
        files = _image_files(path)
        assert {name for name in files if name.endswith(".MNG")} == catalogs


def test_conflicting_catalog_records_do_not_guess_song_titles(tmp_path):
    source = tmp_path / "Album"
    source.mkdir()
    (source / "FIRST.MID").write_bytes(_midi_bytes("Embedded title"))
    _write_mng_catalogs(source, "Catalog album", [
        ("FIRST.MID", "Conflicting title one"), ("first.mid", "Conflicting title two"),
    ])
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_content="midi", output_ext="img",
    )
    assert _image_songs_and_metadata(result.output_paths[0], "midi")[0] == ["Embedded title"]


def test_catalog_album_and_song_titles_survive_shuffle_and_capacity_splits(tmp_path, monkeypatch):
    source = tmp_path / "DSKA001"
    source.mkdir()
    records = [(f"0{index}.MID", f"Catalog song {index}") for index in range(1, 4)]
    _write_mng_catalogs(source, "Catalog album", records)
    for filename, _title in records:
        (source / filename).write_bytes(_midi_bytes("Embedded title"))
    monkeypatch.setattr(emulator_image_builder, "PIANODIR_MAX_TRACKS", 2)
    monkeypatch.setattr(emulator_image_builder.random, "shuffle", lambda paths: paths.reverse())
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_ext="img",
        shuffle=True, include_song_lists=True,
    )
    assert result.images_created == 2
    images = [_image_songs_and_metadata(path, "eseq") for path in result.output_paths]
    assert [titles for titles, metadata in images] == [
        ["Catalog song 3", "Catalog song 2"], ["Catalog song 1"],
    ]
    assert all(metadata.disk_title == "Catalog album" for titles, metadata in images)
    text = Path(result.song_list_path).read_text(encoding="utf-8")
    assert text.count("Album: Catalog album\n") == 2
    assert text.index("Catalog song 3") < text.index("Catalog song 2") < text.index("Catalog song 1")


@pytest.mark.parametrize("include_song_lists", [False, True])
def test_midi_compilation_catalogs_cover_all_songs_and_local_title_edits(
    tmp_path, monkeypatch, include_song_lists,
):
    source = tmp_path / "Collection"
    for index in range(1, 4):
        folder = source / f"Album {index}"
        folder.mkdir(parents=True)
        (folder / "FIRST.MID").write_bytes(_midi_bytes(f"Embedded title {index}"))
        if index < 3:
            _write_mng_catalogs(folder, f"Album title {index}", [("FIRST.MID", f"Catalog title {index}")])
    _write_index_csv(source / "Album 2", [{"output_file": "FIRST.MID", "title": "Edited title"}])
    monkeypatch.setattr(emulator_image_builder.random, "shuffle", lambda paths: paths.reverse())
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="fill", output_ext="img", output_content="midi",
        prefix="MIX", starting_number=7, shuffle=True, include_song_lists=include_song_lists,
    )
    assert result.images_created == 1
    assert bool(result.song_list_path) == include_song_lists
    files = _image_files(result.output_paths[0])
    catalog = parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])
    assert [song.filename for song in catalog] == [name for name in files if name.endswith(".MID")]
    assert [song.title for song in catalog] == ["Embedded title 3", "Edited title", "Catalog title 1"]
    assert parse_smart_pianosoft_disk_title(files["PDISK.MNG"]) == "MIX0007"
    assert _image_songs_and_metadata(result.output_paths[0], "midi")[0] == [song.title for song in catalog]


def test_midi_catalog_space_forces_split_before_using_safety_reserve(tmp_path, monkeypatch):
    source = tmp_path / "Album"
    source.mkdir()
    records = [("FIRST.MID", "First"), ("SECOND.MID", "Second")]
    for filename, title in records:
        (source / filename).write_bytes(_large_midi_bytes(title, 5000))
    _write_mng_catalogs(source, "Album title", records)
    # Retain the smaller observed PDISK variant byte-for-byte on both parts.
    disk_catalog = (source / "PDISK.MNG").read_bytes()[:114]
    (source / "PDISK.MNG").write_bytes(disk_catalog)
    blank = tmp_path / "blank.img"
    create_blank_floppy_image(blank, _disk_format())
    listing = read_image_listing(blank)
    # Both MIDI files fit exactly above the reserve if catalogs are ignored.
    margin = listing.free_space - sum(
        allocated_size((source / name).stat().st_size, listing.cluster_size) for name, title in records
    )
    monkeypatch.setattr(emulator_image_builder.random, "shuffle", lambda paths: paths.reverse())
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_ext="img", output_content="midi",
        safety_margin_bytes=margin, shuffle=True, include_song_lists=False,
    )
    assert result.images_created == 2
    for path, expected_title in zip(result.output_paths, ["Second", "First"]):
        files = _image_files(path)
        song, = parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])
        assert song.title == expected_title
        assert song.filename in files
        assert files["PDISK.MNG"] == disk_catalog
        assert read_image_listing(path).free_space >= margin


def test_midi_catalogs_reserve_root_directory_slots(tmp_path):
    source = tmp_path / "Album"
    source.mkdir()
    records = [(f"SONG{index:03d}.MID", f"Song {index}") for index in range(110)]
    for filename, title in records:
        (source / filename).write_bytes(_midi_bytes(title))
    _write_mng_catalogs(source, "Full directory", records)
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_ext="img", output_content="midi",
        disk_format=_disk_format(),
    )
    assert result.images_created == 2
    counts = []
    for path in result.output_paths:
        files = _image_files(path)
        songs = parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])
        counts.append(len(songs))
        assert {song.filename for song in songs} == {name for name in files if name.endswith(".MID")}
        assert parse_smart_pianosoft_disk_title(files["PDISK.MNG"]) == "Full directory"
    # 112 FAT root entries: one label, two catalogs, and 109 songs.
    assert counts == [109, 1]


def _damaged_midi_bytes(kind):
    if kind == "running_status":
        track = b"\x00\x3c\x40"  # Data bytes with no preceding channel status.
    else:
        track = b"\x00\xff\x03\x03Old" if kind == "after_title" else b""
        track += b"-=[BAD SECTOR]=-" * 32
    track += b"\x00\xff\x2f\x00"
    return _midi_bytes("")[:14] + b"MTrk" + len(track).to_bytes(4, "big") + track


@pytest.mark.parametrize("damage", ["before_title", "after_title", "running_status"])
@pytest.mark.parametrize("output_ext", ["img", "hfe"])
def test_damaged_cataloged_midi_is_preserved_with_titles_and_image_warnings(
    tmp_path, damage, output_ext,
):
    source = tmp_path / "Collection"
    album = source / "Album"
    album.mkdir(parents=True)
    damaged_path = album / "01 - Catalog title.mid"
    original = _damaged_midi_bytes(damage)
    damaged_path.write_bytes(original)
    (album / "02 - Healthy.mid").write_bytes(_midi_bytes("Old healthy title"))
    _write_mng_catalogs(album, "Catalog album", [("01.MID", "Catalog title"), ("02.MID", "Healthy")])
    # Explicit edits still reach the catalog when the song cannot be rewritten.
    _write_index_csv(album, [{"output_file": damaged_path.name, "title": "Edited catalog title"}])
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_ext=output_ext,
        output_content="midi", include_song_lists=output_ext == "img",
    )
    assert result.images_created == 1
    assert result.files_prepared == 2
    files = _image_files(result.output_paths[0])
    damaged_song, healthy_song = parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])
    assert damaged_song.title == "Edited catalog title"
    assert healthy_song.title == "Healthy"
    assert files[damaged_song.filename] == original
    assert damaged_path.read_bytes() == original
    assert len(result.warnings) == 1
    warning, = result.warnings
    assert f"DSKA0000.{output_ext} / {damaged_song.filename}" in warning
    assert "Album/01 - Catalog title.mid" in warning
    assert "preserved unchanged" in warning
    assert "playback may fail" in warning.lower()
    assert ("Invalid running status" if damage == "running_status" else "[BAD SECTOR]") in warning
    if result.song_list_path:
        report = Path(result.song_list_path).read_text(encoding="utf-8")
        assert "1. Edited catalog title\n2. Healthy\n" in report
        assert "Warnings:\n" in report
        assert warning in report


def test_known_bad_sector_data_cannot_be_converted_to_eseq(tmp_path):
    source = tmp_path / "Album"
    source.mkdir()
    (source / "01.MID").write_bytes(_damaged_midi_bytes("after_title"))
    _write_mng_catalogs(source, "Album", [("01.MID", "Catalog title")])
    output = tmp_path / "images"
    with pytest.raises(FloppyImageError, match="Missing MIDI data cannot be converted to E-SEQ"):
        build_emulator_disk_images(source, output, output_content="eseq", output_ext="img")
    assert list(output.iterdir()) == []


def test_unreadable_embedded_title_without_catalog_does_not_drop_an_index_edit(tmp_path):
    source = tmp_path / "Album"
    source.mkdir()
    (source / "01.MID").write_bytes(_damaged_midi_bytes("running_status"))
    _write_index_csv(source, [{"output_file": "01.MID", "title": "Requested edit"}])
    output = tmp_path / "images"
    with pytest.raises(FloppyImageError, match="Invalid running status"):
        build_emulator_disk_images(source, output, output_content="midi", output_ext="img")
    assert list(output.iterdir()) == []


@pytest.mark.parametrize("output_content", ["midi", "eseq"])
def test_lf_catalogs_match_old_numeric_extraction_names_and_identical_renamed_copies(
    tmp_path, output_content,
):
    source = tmp_path / "Collection"
    album = source / "Artist - Album"
    album.mkdir(parents=True)
    originals = {"01 - 01.mid": _midi_bytes("01"), "02 - 02.mid": _midi_bytes("02")}
    for name, data in originals.items():
        (album / name).write_bytes(data)
    (album / "Manually renamed.mid").write_bytes(originals["01 - 01.mid"])
    _write_mng_catalogs(album, "Catalog album", [
        ("01.MID", "First catalog title"), ("02.MID", "Second catalog title"),
    ])
    for name in ["PSONG.MNG", "PDISK.MNG"]:
        path = album / name
        path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    source_bytes = {path: path.read_bytes() for path in album.iterdir()}
    result = build_emulator_disk_images(
        source, tmp_path / "images", disk_layout="folders", output_ext="hfe",
        output_content=output_content, include_song_lists=True,
    )
    expected = ["First catalog title", "Second catalog title", "First catalog title"]
    assert _image_songs_and_metadata(result.output_paths[0], output_content)[0] == expected
    report = Path(result.song_list_path).read_text(encoding="utf-8")
    assert "Album: Catalog album\n" in report
    assert "1. First catalog title\n2. Second catalog title\n3. First catalog title\n" in report
    if output_content == "midi":
        files = _image_files(result.output_paths[0])
        assert files["PSONG.MNG"].startswith(b"PSONG   MNG   \r\n")
        assert parse_smart_pianosoft_disk_title(files["PDISK.MNG"]) == "Catalog album"
        assert [song.title for song in parse_smart_pianosoft_song_catalog(files["PSONG.MNG"])] == expected
    assert source_bytes == {path: path.read_bytes() for path in source_bytes}


def test_identical_recordings_with_conflicting_catalog_titles_do_not_guess_copies(tmp_path):
    source = tmp_path / "Album"
    source.mkdir()
    data = _midi_bytes("Embedded fallback")
    for name in ["01 - 01.mid", "02 - 02.mid", "Unknown copy.mid"]:
        (source / name).write_bytes(data)
    _write_mng_catalogs(source, "Album", [("01.MID", "First"), ("02.MID", "Second")])
    result = build_emulator_disk_images(source, tmp_path / "images", output_content="midi", output_ext="img")
    assert _image_songs_and_metadata(result.output_paths[0], "midi")[0] == [
        "First", "Second", "Embedded fallback",
    ]


@pytest.mark.parametrize("output_content", ["midi", "eseq"])
def test_single_bad_sector_marker_in_valid_title_is_not_treated_as_recovery_filler(tmp_path, output_content):
    source = tmp_path / "Songs"
    source.mkdir()
    title = "Song -=[BAD SECTOR]=-"
    (source / "Song.mid").write_bytes(_midi_bytes(title))
    result = build_emulator_disk_images(
        source, tmp_path / "images", output_content=output_content, output_ext="img",
    )
    assert result.warnings == ()
    assert _image_songs_and_metadata(result.output_paths[0], output_content)[0] == [title]


@pytest.mark.parametrize("cancel", [False, True])
def test_catalog_fallback_does_not_swallow_write_errors_or_cancellation(tmp_path, monkeypatch, cancel):
    source = tmp_path / "Album"
    source.mkdir()
    (source / "01.MID").write_bytes(_midi_bytes("Embedded title"))
    _write_mng_catalogs(source, "Album", [("01.MID", "Catalog title")])
    failure = FloppyOperationCancelled("cancelled") if cancel else OSError("No space left on device")

    def fail_write(*args, **kwargs):
        raise failure

    monkeypatch.setattr(emulator_image_builder, "write_midi_title_to_path", fail_write)
    output = tmp_path / "images"
    expected_type = FloppyOperationCancelled if cancel else FloppyImageError
    with pytest.raises(expected_type, match=str(failure)):
        build_emulator_disk_images(source, output, output_content="midi", output_ext="img")
    assert list(output.iterdir()) == []
