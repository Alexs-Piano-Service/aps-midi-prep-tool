import os
import re
from dataclasses import dataclass, field

from .eseq_converter import refresh_eseq_timing_fields_in_bytes


PIANODIR_FILENAME = "PIANODIR.FIL"
PIANODIR_ROW_PATH = ":PIANODIR:"
PIANODIR_TARGET_FILE_SIZE = 6 * 1024
PIANODIR_MAX_TRACKS = 60
PIANODIR_HEADER = b"\xFE\x00\x00\x00\x14\x00\x00PIANODIR\x00"
PIANODIR_DISK_METADATA_OFFSET = 0x12D0
PIANODIR_DISK_METADATA_SIZE = 0x40
PIANODIR_TOTAL_DURATION_OFFSET = PIANODIR_DISK_METADATA_OFFSET + 0x40
PIANODIR_SECONDARY_AGGREGATE_OFFSET = PIANODIR_DISK_METADATA_OFFSET + 0x44
PIANODIR_COUNT_OFFSET = PIANODIR_DISK_METADATA_OFFSET + 0x46
PIANODIR_TRACK_SIZE = 0x50
PIANODIR_TRACK_DURATION_OFFSET = 0x10
PIANODIR_TRACK_DELAY_BEFORE_OFFSET = 0x14
PIANODIR_TRACK_SECONDARY_WORD_OFFSET = 0x16
PIANODIR_TRACK_DELAY_AFTER_OFFSET = 0x18
PIANODIR_TRACK_WRITE_PROTECT_OFFSET = 0x28
PIANODIR_TRACK_TYPE_OFFSET = 0x29
ESEQ_ORDER_KEY_OFFSET = 0x27
ESEQ_ORDER_KEY_SIZE = 12
ESEQ_ORDER_KEY_END = ESEQ_ORDER_KEY_OFFSET + ESEQ_ORDER_KEY_SIZE
PIANODIR_TRACK_SOURCE_START = ESEQ_ORDER_KEY_OFFSET
PIANODIR_TRACK_SOURCE_END = PIANODIR_TRACK_SOURCE_START + PIANODIR_TRACK_SIZE
ESEQ_WRITE_PROTECT_OFFSET = PIANODIR_TRACK_SOURCE_START + PIANODIR_TRACK_WRITE_PROTECT_OFFSET
ESEQ_ARRANGEMENT_TYPE_OFFSET = PIANODIR_TRACK_SOURCE_START + PIANODIR_TRACK_TYPE_OFFSET
ESEQ_SIGNATURE = b"COM-ESEQ"
Q11_SIGNATURE = b"Q11V1.00"
ESEQ_ARRANGEMENT_TYPE_LABELS = {
    0: "Solo",
    1: "L-R Split",
    2: "Ensemble",
    3: "Unknown",
}
PIANODIR_CATALOG_AND_TITLE_RE = re.compile(
    r"""
    ^\s*
    (?P<catalog>
        (?=.*\d)
        [A-Z0-9][A-Z0-9 \-]{1,15}?
    )
    (?P<separator>\s{2,}|\s(?=[A-Z][A-Z]))
    (?P<title>.+?)
    \s*$
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class PianodirTrackEntry:
    image_path: str
    local_path: str
    title: str


@dataclass(frozen=True)
class PianodirMetadata:
    catalog_number: str = ""
    disk_title: str = ""
    raw_label_bytes: bytes = field(default=b"", repr=False, compare=False)


def is_pianodir_path(path):
    return os.path.basename(path).upper() == PIANODIR_FILENAME


def is_eseq_filename(path):
    filename = os.path.basename(path)
    stem, ext = os.path.splitext(filename)
    return bool(stem) and ext.lower() in {"", ".fil"}


def is_eseq_bytes(data):
    return len(data) >= PIANODIR_TRACK_SOURCE_END and data[7:15] == ESEQ_SIGNATURE


def is_q11_eseq_bytes(data):
    return is_eseq_bytes(data) and data[0x0F:0x17] == Q11_SIGNATURE


def eseq_arrangement_type_label(code):
    return ESEQ_ARRANGEMENT_TYPE_LABELS.get(int(code) & 0x03, "Unknown")


def extract_eseq_arrangement_type_code_from_bytes(data):
    if is_q11_eseq_bytes(data):
        return 2
    if not is_eseq_bytes(data):
        raise ValueError("File is not a valid E-SEQ file.")
    if len(data) <= ESEQ_ARRANGEMENT_TYPE_OFFSET:
        raise ValueError("File is too small to contain E-SEQ arrangement type metadata.")
    return data[ESEQ_ARRANGEMENT_TYPE_OFFSET] & 0x03


def extract_eseq_arrangement_type_label_from_bytes(data):
    return eseq_arrangement_type_label(extract_eseq_arrangement_type_code_from_bytes(data))


def read_eseq_arrangement_type_label_from_file(path):
    with open(path, "rb") as handle:
        return extract_eseq_arrangement_type_label_from_bytes(handle.read())


def extract_eseq_write_protect_from_bytes(data):
    if is_q11_eseq_bytes(data):
        return None
    if not is_eseq_bytes(data):
        raise ValueError("File is not a valid E-SEQ file.")
    if len(data) <= ESEQ_WRITE_PROTECT_OFFSET:
        raise ValueError("File is too small to contain E-SEQ write-protect metadata.")
    return bool(data[ESEQ_WRITE_PROTECT_OFFSET] & 0x80)


def read_eseq_write_protect_from_file(path):
    with open(path, "rb") as handle:
        return extract_eseq_write_protect_from_bytes(handle.read())


def eseq_type_display_label(file_kind, arrangement_type_label, write_protected=None):
    file_kind = (file_kind or "FIL").strip() or "FIL"
    arrangement_type_label = (arrangement_type_label or "").strip()
    details = []
    if arrangement_type_label:
        details.append(arrangement_type_label)
    if write_protected is not None:
        details.append("WP" if write_protected else "WP Off")
    if not details:
        return file_kind
    return f"{file_kind} ({', '.join(details)})"


def pianodir_is_populated(size_bytes):
    return int(size_bytes or 0) > len(PIANODIR_HEADER)


def _ascii_text(value):
    return (value or "").encode("ascii", errors="replace").decode("ascii", errors="replace")


def _normalize_catalog_number(catalog_text):
    clean_text = _ascii_text(catalog_text).replace("\x00", "").strip()
    if not clean_text:
        return ""
    clean_text = re.sub(r"-\s+", "-", clean_text)
    clean_text = re.sub(r"\s+", "-", clean_text)
    match = re.match(r"^(?P<prefix>[A-Z]{2,5})\s*(?P<number>\d{3,5})(?P<suffix>[A-Z]?)$", clean_text)
    if match and "-" not in clean_text:
        return f"{match.group('prefix')}-{match.group('number')}{match.group('suffix')}"
    return clean_text


def normalize_pianodir_catalog_number(catalog_text):
    return _normalize_catalog_number(catalog_text)


def _decode_disk_label(data):
    block = bytes(data or b"")[PIANODIR_DISK_METADATA_OFFSET:PIANODIR_DISK_METADATA_OFFSET + PIANODIR_DISK_METADATA_SIZE]
    if not block:
        return ""
    return block.split(b"\x00", 1)[0].decode("ascii", errors="replace").rstrip()


def _split_disk_label(label_text, raw_label_bytes=b""):
    clean_text = _ascii_text(label_text).replace("\x00", "").rstrip()
    if not clean_text.strip():
        return PianodirMetadata(raw_label_bytes=raw_label_bytes)

    match = PIANODIR_CATALOG_AND_TITLE_RE.match(clean_text)
    if match:
        catalog_text = _normalize_catalog_number(match.group("catalog"))
        title_text = match.group("title").strip()
        suffix_match = re.match(r"^(?P<token>[A-Z0-9]{1,4})\s{2,}(?P<rest>.+)$", title_text)
        if catalog_text.endswith("-") and suffix_match:
            catalog_text = _normalize_catalog_number(f"{catalog_text}{suffix_match.group('token')}")
            title_text = suffix_match.group("rest").strip()
        return PianodirMetadata(
            catalog_number=catalog_text,
            disk_title=title_text,
            raw_label_bytes=raw_label_bytes,
        )

    fallback_match = re.match(r"^\s*(?P<catalog>(?=.*\d).{1,16}?)\s{2,}(?P<title>.+?)\s*$", clean_text)
    if fallback_match:
        return PianodirMetadata(
            catalog_number=_normalize_catalog_number(fallback_match.group("catalog")),
            disk_title=fallback_match.group("title").strip(),
            raw_label_bytes=raw_label_bytes,
        )

    return PianodirMetadata(disk_title=clean_text.strip(), raw_label_bytes=raw_label_bytes)


def parse_pianodir_metadata(data):
    raw_label = bytes(data or b"")[
        PIANODIR_DISK_METADATA_OFFSET:PIANODIR_DISK_METADATA_OFFSET + PIANODIR_DISK_METADATA_SIZE
    ]
    return _split_disk_label(_decode_disk_label(data), raw_label)


def read_pianodir_metadata_from_file(path):
    with open(path, "rb") as handle:
        return parse_pianodir_metadata(handle.read())


def build_pianodir_metadata_bytes(metadata=None, *, catalog_number="", disk_title=""):
    if metadata is not None:
        if (
            not catalog_number
            and not disk_title
            and getattr(metadata, "raw_label_bytes", b"")
        ):
            return bytes(metadata.raw_label_bytes)[:PIANODIR_DISK_METADATA_SIZE].ljust(
                PIANODIR_DISK_METADATA_SIZE,
                b"\x00",
            )
        catalog_number = metadata.catalog_number
        disk_title = metadata.disk_title

    catalog_text = _normalize_catalog_number(catalog_number)
    title_text = _ascii_text(disk_title).strip()

    if catalog_text and title_text:
        combined = f"{catalog_text:<16}{title_text}"
        if len(combined.encode("ascii", errors="replace")) > PIANODIR_DISK_METADATA_SIZE:
            combined = f"{catalog_text} {title_text}"
    else:
        combined = catalog_text or title_text

    encoded = combined.encode("ascii", errors="replace")[:PIANODIR_DISK_METADATA_SIZE]
    return encoded.ljust(PIANODIR_DISK_METADATA_SIZE, b"\x00")


def _insert_padded_ascii(byte_string, insert_string, start_index, end_index):
    insert_length = end_index - start_index + 1
    encoded = (insert_string or "").encode("ascii", errors="replace")[:insert_length]
    padded = encoded.ljust(insert_length, b" ")
    return byte_string[:start_index] + padded + byte_string[end_index + 1:]


def build_eseq_order_key_from_path(path, *, sort_last=False):
    filename = os.path.basename(path or "")
    stem, ext = os.path.splitext(filename)
    ext = ext.lstrip(".")
    stem = "".join(
        ch.upper() if ch.isascii() and ch.isprintable() else "_"
        for ch in stem
    )
    ext = "".join(
        ch.upper() if ch.isascii() and ch.isprintable() else "_"
        for ch in ext
    )
    if sort_last:
        stem = ("~" + stem)[:8]
    stem_bytes = stem.encode("ascii", errors="replace")[:8].ljust(8, b" ")
    ext_bytes = ext.encode("ascii", errors="replace")[:3].ljust(3, b" ")
    return stem_bytes + ext_bytes + b"\x00"


def build_dos83_name_bytes(path, *, uppercase=False):
    filename = os.path.basename(path or "")
    stem, ext = os.path.splitext(filename)
    ext = ext.lstrip(".")
    if uppercase:
        stem = stem.upper()
        ext = ext.upper()
    stem = "".join(
        ch if ch.isascii() and ch.isprintable() else "_"
        for ch in stem
    )
    ext = "".join(
        ch if ch.isascii() and ch.isprintable() else "_"
        for ch in ext
    )
    stem_bytes = stem.encode("ascii", errors="replace")[:8].ljust(8, b" ")
    ext_bytes = ext.encode("ascii", errors="replace")[:3].ljust(3, b" ")
    return stem_bytes + ext_bytes


def normalize_eseq_order_key(order_key):
    if order_key is None:
        return b""
    if isinstance(order_key, str):
        order_key = order_key.encode("ascii", errors="replace")
    else:
        order_key = bytes(order_key)
    return order_key[:ESEQ_ORDER_KEY_SIZE].ljust(ESEQ_ORDER_KEY_SIZE, b"\x00")


def extract_eseq_order_key_from_bytes(data):
    if len(data) < ESEQ_ORDER_KEY_END:
        raise ValueError("File is too small to contain an E-SEQ order key.")
    return normalize_eseq_order_key(data[ESEQ_ORDER_KEY_OFFSET:ESEQ_ORDER_KEY_END])


def read_eseq_order_key_from_file(path):
    with open(path, "rb") as handle:
        return extract_eseq_order_key_from_bytes(handle.read())


def update_eseq_order_key_to_path(source_path, order_key, dest_path):
    try:
        with open(source_path, "rb") as handle:
            data = bytearray(handle.read())
        if len(data) < ESEQ_ORDER_KEY_END:
            raise ValueError("File is too small to contain an E-SEQ order key.")
        data[ESEQ_ORDER_KEY_OFFSET:ESEQ_ORDER_KEY_END] = normalize_eseq_order_key(order_key)
        try:
            output = refresh_eseq_timing_fields_in_bytes(bytes(data))
        except Exception:
            output = bytes(data)
        with open(dest_path, "wb") as handle:
            handle.write(output)
        return None
    except Exception as exc:
        return f"Error updating {os.path.basename(source_path)}: {exc}"


def update_eseq_order_key(path, order_key):
    return update_eseq_order_key_to_path(path, order_key, path)


def _build_track_entry(track_entry):
    with open(track_entry.local_path, "rb") as handle:
        data = handle.read()

    if not is_eseq_bytes(data):
        raise ValueError(f"{os.path.basename(track_entry.image_path)} is not a valid E-SEQ file.")

    short_name = build_dos83_name_bytes(track_entry.image_path)
    if is_q11_eseq_bytes(data):
        track = bytearray(PIANODIR_TRACK_SIZE)
        track[0x00:0x0B] = short_name
        track[0x0B] = 0x00
        track[0x0C] = data[0x24]
        track[0x1C:0x20] = bytes.fromhex("02 00 00 00")
        track[0x20:0x24] = bytes.fromhex("10 7F 00 00")
        track[0x24:0x28] = bytes.fromhex("41 01 00 00")
        track[0x28:0x2C] = bytes.fromhex("00 02 00 00")
        track[0x30:0x50] = data[0x57:0x77]
        return bytes(track)

    track = bytearray(data[PIANODIR_TRACK_SOURCE_START:PIANODIR_TRACK_SOURCE_END])
    if len(track) != PIANODIR_TRACK_SIZE:
        raise ValueError(f"{os.path.basename(track_entry.image_path)} is too small to build a PIANODIR entry.")
    track[0x00:0x0B] = short_name
    return bytes(track)


def build_pianodir_bytes(track_entries, metadata=None, *, catalog_number="", disk_title=""):
    track_entries = list(track_entries)
    if len(track_entries) > PIANODIR_MAX_TRACKS:
        raise ValueError(f"Yamaha E-SEQ supports at most {PIANODIR_MAX_TRACKS} files per set.")

    output = bytearray(PIANODIR_TARGET_FILE_SIZE)
    output[0:len(PIANODIR_HEADER)] = PIANODIR_HEADER
    total_duration = 0
    secondary_aggregate = 0

    for slot, track_entry in enumerate(track_entries):
        track_bytes = _build_track_entry(track_entry)
        offset = len(PIANODIR_HEADER) + slot * PIANODIR_TRACK_SIZE
        output[offset:offset + PIANODIR_TRACK_SIZE] = track_bytes
        total_duration = (
            total_duration
            + int.from_bytes(
                track_bytes[PIANODIR_TRACK_DURATION_OFFSET:PIANODIR_TRACK_DURATION_OFFSET + 4],
                "little",
            )
        ) & 0xFFFFFFFF
        secondary_aggregate += int.from_bytes(
            track_bytes[
                PIANODIR_TRACK_SECONDARY_WORD_OFFSET:PIANODIR_TRACK_SECONDARY_WORD_OFFSET + 2
            ],
            "little",
        )

    metadata_block = build_pianodir_metadata_bytes(
        metadata,
        catalog_number=catalog_number,
        disk_title=disk_title,
    )
    output[
        PIANODIR_DISK_METADATA_OFFSET:PIANODIR_DISK_METADATA_OFFSET + PIANODIR_DISK_METADATA_SIZE
    ] = metadata_block
    output[PIANODIR_TOTAL_DURATION_OFFSET:PIANODIR_TOTAL_DURATION_OFFSET + 4] = total_duration.to_bytes(4, "little")
    output[PIANODIR_SECONDARY_AGGREGATE_OFFSET:PIANODIR_SECONDARY_AGGREGATE_OFFSET + 2] = min(
        secondary_aggregate,
        0xFFFF,
    ).to_bytes(2, "little")
    output[PIANODIR_COUNT_OFFSET:PIANODIR_COUNT_OFFSET + 2] = (len(track_entries) + 1).to_bytes(2, "little")
    return bytes(output)
