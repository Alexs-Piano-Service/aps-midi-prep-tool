# Copyright 2026 Alex's Piano Service LLC.
# SPDX-License-Identifier: Apache-2.0
"""Offline, read-only metadata extraction from Mark IV PostgreSQL 7.3 disks.

The Mark IV's database is a 32-bit little-endian PostgreSQL 7.3 cluster. We
read its heap pages and transaction status bits; we never launch the old
server, execute SQL, or replay its WAL. This deliberately supports only the
layout observed on the inspected drive. Unknown layouts fall back to file
metadata with a warning, while the original database is still preserved.

Format references: PostgreSQL REL7_3_STABLE src/include/access/htup.h,
storage/bufpage.h, storage/itemid.h, catalog/pg_class.h, pg_attribute.h,
and src/backend/access/transam/clog.c in https://github.com/postgres/postgres.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import os
from pathlib import Path, PurePosixPath
import struct
from typing import Iterator

from .paths import is_link

@dataclass
class MetadataCatalog:
    tracks: dict[str, dict] = field(default_factory=dict)
    albums: dict[str, dict] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    evidence_files: list[str] = field(default_factory=list)


class MetadataError(ValueError):
    """Unsupported or inconsistent disk metadata."""


def decode_text(value: bytes) -> str:
    """Keep Unicode when present, with legacy Japanese/Western fallbacks."""
    for encoding in ("utf-8", "cp932", "cp1252"):
        try:
            return value.decode(encoding).rstrip("\x00").strip()
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8", errors="replace").rstrip("\x00").strip()


def _regular_files(directory: Path) -> Iterator[Path]:
    """Do not follow filesystem links when collecting database evidence."""
    for current, dirs, files in os.walk(directory, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not is_link(Path(current) / d))
        for name in sorted(files):
            path = Path(current) / name
            if not is_link(path) and path.is_file():
                yield path


def _read(path: Path) -> bytes:
    # The caller only passes known metadata files, never executable contents.
    if is_link(path):
        raise MetadataError(f"Refusing metadata symlink: {path.name}")
    return path.read_bytes()


@dataclass(frozen=True)
class _Attribute:
    name: str
    type_oid: int
    length: int
    number: int
    alignment: str
    dropped: bool = False


class _PG73:
    """A small heap reader, not a general PostgreSQL recovery engine."""

    def __init__(self, cluster: Path):
        self.cluster = cluster
        self._clog: dict[int, bytes] = {}
        self.warnings: set[str] = set()
        if is_link(cluster / "base") or is_link(cluster / "pg_clog"):
            raise MetadataError("Refusing symlinked database storage directory")

    def _status(self, xid: int) -> int:
        if xid in (1, 2):  # Bootstrap and frozen transactions are committed.
            return 1
        if xid == 0:
            return 2
        segment = xid // 0x100000
        if segment not in self._clog:
            path = self.cluster / "pg_clog" / f"{segment:04X}"
            try:
                self._clog[segment] = _read(path)
            except OSError as exc:
                raise MetadataError(f"Cannot establish transaction status: {path.name}") from exc
        data = self._clog[segment]
        offset = (xid % 0x100000) // 4
        if offset >= len(data):
            raise MetadataError(f"Transaction status {xid} lies beyond the available log")
        return (data[offset] >> ((xid % 4) * 2)) & 3

    def _visible(self, row: bytes) -> bool:
        xmin, xmax, xvac = struct.unpack_from("<III", row)
        mask = struct.unpack_from("<H", row, 20)[0]
        # Mirrors HeapTupleSatisfiesNow for an offline, stopped server.
        if not mask & 0x0100:
            if mask & 0x0200:
                return False
            if mask & 0x4000:  # VACUUM FULL moved this copy away.
                if self._status(xvac) == 1:
                    return False
            elif mask & 0x8000:
                if self._status(xvac) != 1:
                    return False
            elif self._status(xmin) != 1:
                return False
        if mask & (0x0800 | 0x1000):  # No deletion, or only a row lock.
            return True
        if mask & 0x0400:
            return False
        if mask & 0x0040:
            xmax = xmin
        return self._status(xmax) != 1

    def tuples(self, relation: Path) -> Iterator[bytes]:
        """Read valid active line pointers; never search stale free space."""
        segment = 0
        while True:
            path = relation if segment == 0 else relation.with_name(f"{relation.name}.{segment}")
            if not path.exists():
                if segment == 0:
                    raise MetadataError(f"Missing database relation {relation.name}")
                return
            if is_link(path):
                raise MetadataError(f"Refusing database relation symlink {path.name}")
            with path.open("rb") as stream:
                while page := stream.read(8192):
                    if len(page) != 8192:
                        raise MetadataError(f"Truncated database page in {path.name}")
                    if not any(page):  # Uninitialized relation page.
                        continue
                    lower, upper, special, version = struct.unpack_from("<HHHH", page, 12)
                    if version != 0x2001 or not (20 <= lower <= upper <= special == 8192) or (lower - 20) % 4:
                        raise MetadataError(f"Unsupported/corrupt PostgreSQL page in {path.name}")
                    for pointer in range(20, lower, 4):
                        item = struct.unpack_from("<I", page, pointer)[0]
                        offset, flags, length = item & 0x7FFF, (item >> 15) & 3, item >> 17
                        if flags != 1:
                            continue
                        if length < 23 or offset < upper or offset + length > special:
                            raise MetadataError(f"Invalid tuple pointer in {path.name}")
                        row = page[offset:offset + length]
                        natts, mask, header = struct.unpack_from("<HHB", row, 18)
                        bitmap = (natts + 7) // 8 if mask & 1 else 0
                        required = 23 + bitmap + (4 if mask & 0x10 else 0)
                        if not 0 < natts <= 1600 or not required <= header <= length or header % 4:
                            raise MetadataError(f"Invalid tuple header in {path.name}")
                        if self._visible(row):
                            yield row
            segment += 1

    def relations(self, database: Path) -> dict[str, tuple[int, int]]:
        result = {}
        for row in self.tuples(database / "1259"):
            start = row[22]
            value = row[start:]
            if len(value) < 104 or not struct.unpack_from("<H", row, 20)[0] & 0x10:
                raise MetadataError("Unrecognized pg_class catalog layout")
            name = decode_text(value[:64].split(b"\0", 1)[0])
            if value[102:103] != b"r" or not name.endswith(("_song", "_album")):
                continue
            oid = struct.unpack_from("<I", row, start - 4)[0]
            node = struct.unpack_from("<I", value, 80)[0]
            if name in result and result[name] != (oid, node):
                raise MetadataError(f"Ambiguous live table {name}")
            result[name] = (oid, node)
        return result

    def attributes(self, database: Path, wanted: set[int]) -> dict[int, list[_Attribute]]:
        result: dict[int, list[_Attribute]] = defaultdict(list)
        seen = set()
        for row in self.tuples(database / "1249"):
            value = row[row[22]:]
            if len(value) < 104:
                raise MetadataError("Unrecognized pg_attribute catalog layout")
            oid = struct.unpack_from("<I", value)[0]
            number = struct.unpack_from("<h", value, 78)[0]
            if oid not in wanted or number <= 0:
                continue
            if (oid, number) in seen:
                raise MetadataError("Duplicate active database attribute")
            seen.add((oid, number))
            result[oid].append(_Attribute(
                decode_text(value[4:68].split(b"\0", 1)[0]),
                struct.unpack_from("<I", value, 68)[0],
                struct.unpack_from("<h", value, 76)[0],
                number, chr(value[95]), bool(value[98]),
            ))
        for attrs in result.values():
            attrs.sort(key=lambda attr: attr.number)
            if [attr.number for attr in attrs] != list(range(1, len(attrs) + 1)):
                raise MetadataError("Incomplete table schema in pg_attribute")
        return result

    def values(self, row: bytes, attributes: list[_Attribute]) -> dict:
        natts, mask = struct.unpack_from("<HH", row, 18)
        if natts > len(attributes):
            raise MetadataError("Tuple has more fields than its catalog schema")
        offset = row[22]
        result = {}
        for attr in attributes[:natts]:
            if mask & 1 and not row[23 + (attr.number - 1) // 8] & (1 << ((attr.number - 1) % 8)):
                if not attr.dropped:
                    result[attr.name] = None
                continue
            # Mark IV's i386 ABI aligns doubles and int64 to four bytes.
            alignment = {"c": 1, "s": 2, "i": 4, "d": 4}.get(attr.alignment)
            if alignment is None:
                raise MetadataError(f"Unknown attribute alignment {attr.alignment}")
            offset = (offset + alignment - 1) & ~(alignment - 1)
            if attr.length == -1:
                if offset + 4 > len(row):
                    raise MetadataError("Truncated variable-length attribute")
                header = struct.unpack_from("<I", row, offset)[0]
                length = header & 0x3FFFFFFF
                if length < 4 or offset + length > len(row):
                    raise MetadataError("Invalid variable-length attribute")
                if header & 0xC0000000:
                    value = None
                    if not attr.dropped:
                        self.warnings.add(f"Compressed/external database field {attr.name} could not be decoded; original database is preserved.")
                else:
                    value = decode_text(row[offset + 4:offset + length])
            elif attr.length > 0:
                length = attr.length
                if offset + length > len(row):
                    raise MetadataError("Truncated fixed-length attribute")
                raw = row[offset:offset + length]
                if attr.type_oid in (20, 21, 23):
                    value = int.from_bytes(raw, "little", signed=True)
                elif attr.type_oid == 26:
                    value = int.from_bytes(raw, "little")
                elif attr.type_oid == 16:
                    value = bool(raw[0])
                elif attr.type_oid in (18, 19):
                    value = decode_text(raw.split(b"\0", 1)[0])
                else:
                    # Time and unused fixed fields do not contribute to names.
                    value = None
            else:
                raise MetadataError(f"Unsupported database type for {attr.name}")
            offset += length
            if not attr.dropped:
                result[attr.name] = value
        return result

    def tables(self, database: Path) -> dict[str, list[dict]]:
        relations = self.relations(database)
        attributes = self.attributes(database, {oid for oid, _ in relations.values()})
        result = {}
        for name, (oid, node) in relations.items():
            if oid not in attributes:
                raise MetadataError(f"No schema for {name}")
            result[name] = [self.values(row, attributes[oid]) for row in self.tuples(database / str(node))]
        return result


def _source_directory(value: object, category: str, album_id: object) -> str | None:
    if isinstance(value, str) and value:
        path = PurePosixPath(value.replace("\\", "/"))
        parts = path.parts
        if ".." in parts:
            return None
        if "songs" in parts:
            return PurePosixPath(*parts[parts.index("songs"):]).as_posix().rstrip("/")
        # Paths outside the disk's songs tree describe removable media.
        return None
    if isinstance(album_id, int):
        return f"songs/{category}/{album_id}"
    return None


def _import_tables(catalog: MetadataCatalog, tables: dict[str, list[dict]]) -> None:
    for table, rows in tables.items():
        if not table.endswith("_album"):
            continue
        category = table.removesuffix("_album")
        albums = {}
        for row in rows:
            album_id = row.get("album_id")
            directory = _source_directory(row.get("path"), category, album_id)
            if directory is None:
                continue
            # Leave missing labels empty so the caller can try PDISK/PSONG
            # and MIDI metadata before falling back to numeric directories.
            title = row.get("title") or ""
            metadata = {
                "title": title, "category": category, "album_id": album_id,
                "subtitle": row.get("subtitle") or "", "metadata_source": "postgresql-7.3",
            }
            catalog.albums[directory] = metadata
            albums[album_id] = (directory, title)
        for row in tables.get(f"{category}_song", []):
            album = albums.get(row.get("album_id"))
            filename = row.get("filename")
            if album is None or not isinstance(filename, str) or not filename:
                continue
            filename_path = PurePosixPath(filename.replace("\\", "/"))
            if filename_path.is_absolute() or ".." in filename_path.parts:
                catalog.warnings.append(f"Ignored unsafe database filename in {category}.")
                continue
            directory, album_title = album
            track = {
                "title": row.get("title") or "",
                "album": album_title, "track_number": row.get("display_order"),
                "artist": "", "subtitle": row.get("subtitle") or "", "category": category,
                "song_id": row.get("song_id"), "album_id": row.get("album_id"),
                "metadata_source": "postgresql-7.3", "original_filename": filename,
                "format": row.get("format") or "", "copyright": row.get("copyright") or "",
                "your_record": row.get("your_record") or "", "add_file": row.get("add_file") or "",
            }
            catalog.tracks[f"{directory}/{filename_path.as_posix()}"] = track
            # Most synchronized audio shares the MIDI stem. Explicit add_file
            # values can identify companions whose basenames differ.
            companion = row.get("add_file")
            if isinstance(companion, str) and PurePosixPath(companion).suffix.lower() in (".wav", ".mp3", ".mid", ".fil"):
                companion_path = PurePosixPath(companion.replace("\\", "/"))
                if not companion_path.is_absolute() and ".." not in companion_path.parts:
                    catalog.tracks[f"{directory}/{companion_path.as_posix()}"] = dict(track)


def load_metadata(root: Path) -> MetadataCatalog:
    """Map source-relative album/song paths to labels from the disk database.

    Database errors are explicit warnings; they never stop the independent
    filesystem inventory or cause music to be excluded from the backup.
    """
    root = Path(root).resolve()
    catalog = MetadataCatalog()
    cluster = root / "local/pgsql/data"
    schema = root / "songs/tool"
    if schema.is_dir() and not is_link(schema) and not is_link(root / "songs"):
        catalog.evidence_files.extend(path.relative_to(root).as_posix() for path in sorted(schema.glob("*.sql")) if path.is_file() and not is_link(path))
    if not cluster.is_dir() or any(is_link(path) for path in (root / "local", root / "local/pgsql", cluster)):
        catalog.warnings.append("No readable Mark IV PostgreSQL database found; using management files and filenames.")
        return catalog
    try:
        catalog.evidence_files.extend(path.relative_to(root).as_posix() for path in _regular_files(cluster))
        if _read(cluster / "PG_VERSION").strip() != b"7.3":
            raise MetadataError("Only the inspected PostgreSQL 7.3 disk layout is supported")
        pg = _PG73(cluster)
        found = False
        databases = sorted(path for path in (cluster / "base").iterdir() if path.is_dir() and not is_link(path) and path.name.isdigit())
        for database in databases:
            relations = pg.relations(database)
            if not any(name in relations for name in ("user_song", "pianosoft_song", "demo_song")):
                continue
            _import_tables(catalog, pg.tables(database))
            found = True
        catalog.warnings.extend(sorted(pg.warnings))
        if not found:
            catalog.warnings.append("No Mark IV music tables found in the database; using management files and filenames.")
    except (OSError, MetadataError, struct.error) as exc:
        catalog.warnings.append(f"Database metadata could not be fully decoded: {exc}. Using available management files and filenames.")
    return catalog
