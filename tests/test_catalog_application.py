"""Catalog application follows song identities and leaves uncertain files alone."""

from dataclasses import replace

import pytest

from aps_midi_prep_tool_app.catalog_application import (
    CatalogCandidate, plan_catalog_application,
)
from aps_midi_prep_tool_app.catalog_inspection import inspect_catalog
from aps_midi_prep_tool_app.smart_pianosoft import build_smart_pianosoft_song_record


def song_catalog(records):
    header = bytearray((b" " * 14 + b"\r\n") * 8)
    header[:48] = (f"PSONG   MNG   \r\nMAX{len(records):03d}        \r\n"
                   f"FILE{len(records):03d}       \r\n").encode("ascii")
    return bytes(header) + b"".join(
        build_smart_pianosoft_song_record(filename, title)
        for filename, title in records
    )


def inspect_songs(*records):
    return inspect_catalog(song_catalog(records))


def disk_catalog(title):
    payload = bytearray((b" " * 14 + b"\r\n") * 8)
    payload[:48] = b"PDISK   MNG   \r\nP.PLAYER      \r\nVer1.01DMV0.53\r\n"
    payload[0x30:0x70] = title.encode("cp1252").ljust(64)
    payload[0x70:0x72] = b"\r\n"
    return bytes(payload)


def test_catalog_title_follows_filename_not_list_order():
    catalog = inspect_songs(("SECOND.MID", "First song"), ("FIRST.MID", "Second song"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("first", "/folder/first.mid"),
        CatalogCandidate("second", "SECOND.mid"),
    ])
    assert [(update.key, update.title) for update in plan.updates] == [
        ("second", "First song"), ("first", "Second song")]
    assert not plan.unmatched_candidates
    assert not plan.missing_songs
    assert not plan.ambiguous_songs


def test_original_filename_alias_matches_staged_rename():
    catalog = inspect_songs(("SONG.MID", "Original title"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate(7, "Renamed study.mid", aliases=(r"C:\Album\song.mid",)),
    ])
    assert plan.updates[0].key == 7
    assert plan.updates[0].matched_by == "filename"


def test_known_catalog_identity_overrides_staged_name_and_aliases():
    catalog = inspect_songs(("ONE.MID", "First"), ("TWO.MID", "Second"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("source", "TWO.MID", aliases=("02 - Something.mid",),
                         catalog_filename="one.mid"),
    ])
    assert [(update.key, update.title) for update in plan.updates] == [("source", "First")]
    assert plan.updates[0].matched_by == "catalog_identity"
    assert [song.filename for song in plan.missing_songs] == ["TWO.MID"]


def test_unknown_known_identity_does_not_fall_back_to_another_song():
    catalog = inspect_songs(("SONG.MID", "Title"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("source", "SONG.MID", catalog_filename="ABSENT.MID"),
    ])
    assert not plan.updates
    assert plan.unmatched_candidates == ("source",)
    assert plan.missing_songs == catalog.songs


def test_extracted_names_match_explicit_record_number_even_in_reordered_subset():
    catalog = inspect_songs(("A.MID", "One"), ("B.MID", "Two"), ("C.MID", "Three"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("third", "03 - Renamed song.midi"),
        CatalogCandidate("first", "01 - Extracted title.mid"),
    ])
    assert [(update.key, update.title, update.track_number) for update in plan.updates] == [
        ("first", "One", 1), ("third", "Three", 3)]
    assert [song.filename for song in plan.missing_songs] == ["B.MID"]
    assert all(update.matched_by == "extracted_track" for update in plan.updates)


def test_deleted_blank_catalog_slot_does_not_shift_extractor_number():
    raw = bytearray(song_catalog([("A.MID", "First"), ("B.MID", "Second"), ("C.MID", "Third")]))
    raw[0x80 + 0xb0:0x80 + 2 * 0xb0] = (b" " * 14 + b"\r\n") * 11
    plan = plan_catalog_application(inspect_catalog(raw), [CatalogCandidate("third", "03 - Study.mid")])
    assert plan.updates[0].catalog_filename == "C.MID"
    assert plan.updates[0].track_number == 3


@pytest.mark.parametrize("name", ["1.mid", "01.mid", "1 - Title.mid", "01 Title.mid", "Title.mid", "01 - Title.wav"])
def test_arbitrary_number_or_row_position_is_not_a_match(name):
    catalog = inspect_songs(("SONG.MID", "Title"))
    plan = plan_catalog_application(catalog, [CatalogCandidate("source", name)])
    assert not plan.updates
    assert plan.missing_songs == catalog.songs


def test_filename_match_takes_precedence_over_numbered_alias():
    catalog = inspect_songs(("FIRST.MID", "First"), ("SECOND.MID", "Second"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("source", "FIRST.MID", aliases=("02 - Renamed.mid",)),
    ])
    assert [update.title for update in plan.updates] == ["First"]
    assert [song.title for song in plan.missing_songs] == ["Second"]


def test_conflicting_filename_aliases_are_ambiguous_and_leave_both_songs_alone():
    catalog = inspect_songs(("FIRST.MID", "First"), ("SECOND.MID", "Second"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("source", "FIRST.MID", aliases=("SECOND.MID",)),
    ])
    assert not plan.updates
    assert plan.ambiguous_songs == catalog.songs
    assert plan.unmatched_candidates == ("source",)


def test_two_candidates_for_the_same_catalog_record_are_ambiguous():
    catalog = inspect_songs(("SONG.MID", "Title"), ("OTHER.MID", "Other title"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("first", "SONG.MID"),
        CatalogCandidate("copy", "01 - Title.mid"),
        CatalogCandidate("other", "other.mid"),
    ])
    assert [update.key for update in plan.updates] == ["other"]
    assert [song.filename for song in plan.ambiguous_songs] == ["SONG.MID"]
    assert plan.unmatched_candidates == ("first", "copy")


def test_duplicate_catalog_names_cannot_be_disambiguated_by_extracted_number():
    catalog = inspect_songs(("SONG.MID", "First title"), ("song.mid", "Other title"))
    plan = plan_catalog_application(catalog, [CatalogCandidate("first", "01 - Study.mid")])
    assert not plan.updates
    assert plan.ambiguous_songs == catalog.songs


def test_duplicate_candidate_keys_never_produce_conflicting_updates():
    catalog = inspect_songs(("FIRST.MID", "First"), ("SECOND.MID", "Second"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("same-row", "FIRST.MID"),
        CatalogCandidate("same-row", "SECOND.MID"),
    ])
    assert not plan.updates
    assert plan.ambiguous_songs == catalog.songs


@pytest.mark.parametrize("filename", ["../X.MID", "A/B.MID", "A\\B.MID", "C:X.MID", "CON.MID", "LPT1.MID", "BAD?.MID", "SONG.FIL"])
def test_unsafe_or_non_midi_catalog_records_are_reported_not_matched(filename):
    catalog = inspect_songs((filename, "Do not apply"), ("GOOD.MID", "Valid title"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("bad", filename), CatalogCandidate("good", "GOOD.MID"),
    ])
    assert [update.key for update in plan.updates] == ["good"]
    assert [song.filename for song in plan.invalid_songs] == [filename]
    assert plan.unmatched_candidates == ("bad",)


def test_empty_title_does_not_erase_existing_midi_metadata():
    catalog = inspect_songs(("SONG.MID", ""))
    plan = plan_catalog_application(catalog, [CatalogCandidate("source", "SONG.MID")])
    assert not plan.updates
    assert plan.empty_title_songs == catalog.songs
    assert plan.unmatched_candidates == ("source",)


def test_missing_songs_and_unmatched_files_are_reported_independently():
    catalog = inspect_songs(("SONG.MID", "Title"), ("ABSENT.MID", "Absent"))
    plan = plan_catalog_application(catalog, [
        CatalogCandidate("source", "SONG.MID"), CatalogCandidate("unrelated", "UNRELATED.MID"),
    ])
    assert [update.key for update in plan.updates] == ["source"]
    assert [song.filename for song in plan.missing_songs] == ["ABSENT.MID"]
    assert plan.unmatched_candidates == ("unrelated",)


@pytest.mark.parametrize("line_endings", ["crlf", "lf"])
def test_disk_catalog_provides_album_title_only(line_endings):
    raw = disk_catalog("Café piano")
    if line_endings == "lf":
        raw = raw.replace(b"\r\n", b"\n")
    catalog = inspect_catalog(raw)
    plan = plan_catalog_application(catalog, [CatalogCandidate("source", "SONG.MID")])
    assert plan.album_title == "Café piano"
    assert not plan.updates
    assert not plan.unmatched_candidates
    assert catalog.raw == raw


def test_empty_album_title_does_not_erase_existing_album_name():
    plan = plan_catalog_application(inspect_catalog(disk_catalog("")))
    assert plan.album_title is None


def test_unknown_catalog_kind_is_rejected():
    catalog = replace(inspect_songs(("SONG.MID", "Title")), kind="OTHER.MNG")
    with pytest.raises(ValueError, match="Only PDISK.MNG and PSONG.MNG"):
        plan_catalog_application(catalog)
