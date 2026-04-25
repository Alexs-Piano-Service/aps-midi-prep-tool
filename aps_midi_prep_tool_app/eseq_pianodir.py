import os
import re
from dataclasses import dataclass


PIANODIR_FILENAME = "PIANODIR.FIL"
PIANODIR_ROW_PATH = ":PIANODIR:"
PIANODIR_TARGET_FILE_SIZE = 6 * 1024
PIANODIR_MAX_TRACKS = 60
PIANODIR_HEADER = b"\xFE\x00\x00\x00\x14\x00\x00PIANODIR\x00"
PIANODIR_DISK_METADATA_OFFSET = 0x12D0
PIANODIR_DISK_METADATA_SIZE = 48
PIANODIR_TRACK_SIZE = 0x50
ESEQ_ORDER_KEY_OFFSET = 0x27
ESEQ_ORDER_KEY_SIZE = 12
ESEQ_ORDER_KEY_END = ESEQ_ORDER_KEY_OFFSET + ESEQ_ORDER_KEY_SIZE
PIANODIR_TRACK_SOURCE_START = ESEQ_ORDER_KEY_OFFSET
PIANODIR_TRACK_SOURCE_END = PIANODIR_TRACK_SOURCE_START + PIANODIR_TRACK_SIZE
PIANODIR_TRACK_TEMPLATE = (
    b"00000000000\x00X\x04\x04\x00TRK\x00\x00\x00\x00\x00\x00\x00ZZ\x00w\x00\x00\x10\x7f\x00\x00A\x01\x00\x00\x80\x00\x00\x00\x00\x00\x00\x00 "
)
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


def is_pianodir_path(path):
    return os.path.basename(path).upper() == PIANODIR_FILENAME


def is_eseq_filename(path):
    filename = os.path.basename(path)
    stem, ext = os.path.splitext(filename)
    return bool(stem) and ext.lower() in {"", ".fil"}


def pianodir_is_populated(size_bytes):
    return int(size_bytes or 0) > len(PIANODIR_HEADER)


def _ascii_text(value):
    return (value or "").encode("ascii", errors="replace").decode("ascii", errors="replace")


def _decode_disk_label(data):
    block = bytes(data or b"")[PIANODIR_DISK_METADATA_OFFSET:PIANODIR_DISK_METADATA_OFFSET + PIANODIR_DISK_METADATA_SIZE]
    if not block:
        return ""
    return block.split(b"\x00", 1)[0].decode("ascii", errors="replace").rstrip()


def _split_disk_label(label_text):
    clean_text = _ascii_text(label_text).replace("\x00", "").rstrip()
    if not clean_text.strip():
        return PianodirMetadata()

    match = PIANODIR_CATALOG_AND_TITLE_RE.match(clean_text)
    if match:
        catalog_text = match.group("catalog").strip()
        title_text = match.group("title").strip()
        suffix_match = re.match(r"^(?P<token>[A-Z0-9]{1,4})\s{2,}(?P<rest>.+)$", title_text)
        if catalog_text.endswith("-") and suffix_match:
            catalog_text = f"{catalog_text} {suffix_match.group('token')}".strip()
            title_text = suffix_match.group("rest").strip()
        return PianodirMetadata(
            catalog_number=catalog_text,
            disk_title=title_text,
        )

    fallback_match = re.match(r"^\s*(?P<catalog>(?=.*\d).{1,16}?)\s{2,}(?P<title>.+?)\s*$", clean_text)
    if fallback_match:
        return PianodirMetadata(
            catalog_number=fallback_match.group("catalog").strip(),
            disk_title=fallback_match.group("title").strip(),
        )

    return PianodirMetadata(disk_title=clean_text.strip())


def parse_pianodir_metadata(data):
    return _split_disk_label(_decode_disk_label(data))


def read_pianodir_metadata_from_file(path):
    with open(path, "rb") as handle:
        return parse_pianodir_metadata(handle.read())


def build_pianodir_metadata_bytes(metadata=None, *, catalog_number="", disk_title=""):
    if metadata is not None:
        catalog_number = metadata.catalog_number
        disk_title = metadata.disk_title

    catalog_text = _ascii_text(catalog_number).strip()
    title_text = _ascii_text(disk_title).strip()

    if catalog_text and title_text:
        combined = f"{catalog_text}   {title_text}"
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
        with open(dest_path, "wb") as handle:
            handle.write(data)
        return None
    except Exception as exc:
        return f"Error updating {os.path.basename(source_path)}: {exc}"


def update_eseq_order_key(path, order_key):
    return update_eseq_order_key_to_path(path, order_key, path)


def _build_track_entry(track_entry):
    with open(track_entry.local_path, "rb") as handle:
        data = handle.read()

    if len(data) < PIANODIR_TRACK_SOURCE_END:
        raise ValueError(f"{os.path.basename(track_entry.image_path)} is too small to build a PIANODIR entry.")

    track = bytes(data[PIANODIR_TRACK_SOURCE_START:PIANODIR_TRACK_SOURCE_END])

    checksum_triplet = [data[55], data[56], data[57]]
    return track, checksum_triplet


def build_pianodir_bytes(track_entries, metadata=None, *, catalog_number="", disk_title=""):
    if len(track_entries) > PIANODIR_MAX_TRACKS:
        raise ValueError(f"Yamaha E-SEQ supports at most {PIANODIR_MAX_TRACKS} files per set.")

    data = bytearray()
    data.extend(PIANODIR_HEADER)

    checksum_totals = [0, 0, 0]
    counter = 1

    for track_entry in track_entries:
        track_bytes, checksum_triplet = _build_track_entry(track_entry)
        data.extend(track_bytes)
        checksum_totals[0] += checksum_triplet[0]
        checksum_totals[1] += checksum_triplet[1]
        checksum_totals[2] += checksum_triplet[2]
        counter += 1

    while len(data) < PIANODIR_TARGET_FILE_SIZE:
        if len(data) == 4880:
            data.extend(
                bytes(
                    [
                        checksum_totals[0] % 256,
                        checksum_totals[1] % 256,
                        checksum_totals[2] % 256,
                        0x00,
                        0x00,
                        0x00,
                        counter % 256,
                    ]
                )
            )
        else:
            data.append(0x00)

    output = bytearray(data[:PIANODIR_TARGET_FILE_SIZE])
    metadata_block = build_pianodir_metadata_bytes(
        metadata,
        catalog_number=catalog_number,
        disk_title=disk_title,
    )
    output[
        PIANODIR_DISK_METADATA_OFFSET:PIANODIR_DISK_METADATA_OFFSET + PIANODIR_DISK_METADATA_SIZE
    ] = metadata_block
    return bytes(output)
