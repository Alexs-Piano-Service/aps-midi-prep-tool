"""Reversible destination preparation, separate from deliberate song edits.

Snapshots retain the bytes before preparation. A three-way merge removes only
the automatic changes, carrying later titles, filenames, ordering and explicit
file transformations back onto that baseline. No reverse conversion is needed.
"""

import copy
import os
from dataclasses import dataclass

from PySide6.QtCore import Qt

from .eseq_pianodir import PIANODIR_ROW_PATH


PREPARATION_ID_ROLE = Qt.UserRole + 73
_MISSING = object()
_SONG_MAPS = (
    "pendingEdits", "pendingRegularConversions", "pendingRegularRenames",
    "pendingRegularOrderKeyEdits", "listedFileInfo", "pendingImageRenames",
    "pendingImageTitleEdits", "pendingImageAdditions", "pendingImageReplacements",
    "pendingImageExportFilenames", "pendingSmartPianoSoftTitleEdits", "imageFileInfo",
)


def _identity(row):
    return row[1].data(PREPARATION_ID_ROLE) if len(row) > 1 and row[1] is not None else None


def _path(row):
    return row[1].text() if len(row) > 1 and row[1] is not None else ""


def _rows(snapshot):
    return {_identity(row): row for row in snapshot["rows"]
            if _identity(row) and _path(row) != PIANODIR_ROW_PATH}


def _clone_row(row):
    return [item.clone() if item is not None else None for item in row]


def _material_state(snapshot):
    state = copy.deepcopy(snapshot["state"])
    copies = snapshot.get("copies", {})
    for field in ("pendingImageAdditions", "pendingImageReplacements"):
        if field in state:
            state[field] = {key: copies.get(path, path) for key, path in state[field].items()}
    for item in state.get("pendingRegularConversions", {}).values():
        item["temp_path"] = copies.get(item.get("temp_path"), item.get("temp_path"))
    catalog = state.get("pendingSmartPianoSoftCatalogReplacement", "")
    if catalog:
        state["pendingSmartPianoSoftCatalogReplacement"] = copies.get(catalog, catalog)
    return state


def _merge(before, after, current):
    if current == after:
        return _MISSING if before is _MISSING else copy.deepcopy(before)
    if isinstance(current, dict) and (isinstance(after, dict) or after is _MISSING):
        original = before if isinstance(before, dict) else {}
        prepared = after if isinstance(after, dict) else {}
        result = {}
        for key in original.keys() | prepared.keys() | current.keys():
            value = _merge(original.get(key, _MISSING), prepared.get(key, _MISSING), current.get(key, _MISSING))
            if value is not _MISSING:
                result[key] = value
        return result
    if isinstance(before, set) and isinstance(after, set) and isinstance(current, set):
        return (before - (after - current)) | (current - after)
    return _MISSING if current is _MISSING else copy.deepcopy(current)


def _same_bytes(first, second):
    if first == second:
        return True
    if not first or not second:
        return False
    try:
        if os.path.getsize(first) != os.path.getsize(second):
            return False
        with open(first, "rb") as left, open(second, "rb") as right:
            while True:
                chunk = left.read(65536)
                if chunk != right.read(65536):
                    return False
                if not chunk:
                    return True
    except OSError:
        return False


def _remap_state(state, aliases):
    state = copy.deepcopy(state)
    for field in _SONG_MAPS:
        if field in state:
            state[field] = {aliases.get(path, path): value for path, value in state[field].items()}
    for field in ("pendingImageDeletes", "loadedRegularEseqPaths"):
        if field in state:
            state[field] = type(state[field])(aliases.get(path, path) for path in state[field])
    return state


@dataclass(frozen=True)
class PreparationLayer:
    before: dict
    after: dict

    def without_paths(self, paths, current_rows):
        """Committed songs become new sources; unfinished songs retain provenance."""
        committed = {_identity(row) for row in current_rows if _path(row) in paths}
        before_ids = set(_rows(self.before))
        if not committed & before_ids:
            return self

        def prune(snapshot):
            removed = {_path(row) for row in snapshot["rows"] if _identity(row) in committed}
            result = dict(snapshot)
            result["rows"] = [row for row in snapshot["rows"] if _identity(row) not in committed]
            result["state"] = copy.deepcopy(snapshot["state"])
            for field in _SONG_MAPS:
                for path in removed:
                    result["state"].get(field, {}).pop(path, None)
            if "pendingImageDeletes" in result["state"]:
                result["state"]["pendingImageDeletes"].difference_update(removed)
            if "loadedRegularEseqPaths" in result["state"]:
                result["state"]["loadedRegularEseqPaths"] = tuple(
                    path for path in result["state"]["loadedRegularEseqPaths"] if path not in removed)
            return result

        before, after = prune(self.before), prune(self.after)
        # A generated catalog row is not an unsaved song.
        if not any(path in before["state"].get("listedFileInfo", {}) or
                   path in before["state"].get("imageFileInfo", {})
                   for path in map(_path, before["rows"])):
            return None
        return PreparationLayer(before, after)

    def rebase(self, current, *, raw_title_role, edited_title_role, image_mode):
        originals, prepared, latest = _rows(self.before), _rows(self.after), _rows(current)
        aliases = {_path(row): _path(originals[identity])
                   for rows in (prepared, latest) for identity, row in rows.items()
                   if identity in originals}
        baseline = _material_state(self.before)
        after = _remap_state(_material_state(self.after), aliases)
        now = _remap_state(_material_state(current), aliases)
        merged = _merge(baseline, after, now)

        # Temp names are not edit provenance. Undo can relocate identical bytes;
        # title/rename edits should never accidentally retain an automatic format
        # conversion merely because its scratch filename changed.
        explicit_material = set()
        for field in ("pendingRegularConversions", "pendingImageAdditions", "pendingImageReplacements"):
            target = merged.setdefault(field, {})
            for path in set(baseline.get(field, {})) | set(after.get(field, {})) | set(now.get(field, {})):
                previous = after.get(field, {}).get(path)
                latest_value = now.get(field, {}).get(path)
                previous_path = previous.get("temp_path") if field == "pendingRegularConversions" and previous else previous
                latest_path = latest_value.get("temp_path") if field == "pendingRegularConversions" and latest_value else latest_value
                if _same_bytes(previous_path, latest_path):
                    if path in baseline.get(field, {}):
                        target[path] = copy.deepcopy(baseline[field][path])
                    else:
                        target.pop(path, None)
                elif latest_value is not None:
                    target[path] = copy.deepcopy(latest_value)
                    explicit_material.add(path)
                else:
                    target.pop(path, None)

        info_field = "imageFileInfo" if image_mode else "listedFileInfo"
        for path in explicit_material:
            if path in now.get(info_field, {}):
                merged.setdefault(info_field, {})[path] = copy.deepcopy(now[info_field][path])

        rows = {}
        for identity, current_row in latest.items():
            original = originals.get(identity)
            automatic = prepared.get(identity)
            if original is None:
                if automatic is None:  # A deliberate addition since preparation.
                    rows[identity] = _clone_row(current_row)
                continue
            path = _path(original)
            row = _clone_row(current_row if path in explicit_material else original)
            row[1].setText(path)
            if automatic is not None:
                def raw(items):
                    item = items[4]
                    value = item.data(raw_title_role) if item is not None else ""
                    return item.text() if value is None and item is not None else value

                if raw(current_row) != raw(automatic):
                    title = raw(current_row)
                    row[4] = current_row[4].clone()
                    row[4].setData(edited_title_role, True)
                    title_field = "pendingImageTitleEdits" if image_mode else "pendingEdits"
                    if image_mode and path in now.get("pendingSmartPianoSoftTitleEdits", {}):
                        title_field = "pendingSmartPianoSoftTitleEdits"
                    merged.setdefault(title_field, {})[path] = title

                name = current_row[3].text() if current_row[3] is not None else os.path.basename(path)
                auto_name = automatic[3].text() if automatic[3] is not None else name
                original_name = original[3].text() if original[3] is not None else os.path.basename(path)
                if name != auto_name:
                    # A renamed prepared .FIL becomes a renamed original .mid;
                    # the next destination will choose its own required suffix.
                    stem, suffix = os.path.splitext(name)
                    old_suffix, auto_suffix = os.path.splitext(original_name)[1], os.path.splitext(auto_name)[1]
                    if path not in explicit_material and old_suffix.lower() != auto_suffix.lower() and suffix.lower() == auto_suffix.lower():
                        name = stem + old_suffix
                    row[3] = current_row[3].clone()
                    row[3].setText(name)
                    if image_mode:
                        merged.setdefault("pendingImageRenames", {})[path] = os.path.join(os.path.dirname(path), name).replace("\\", "/")
                    else:
                        merged.setdefault("pendingRegularRenames", {})[path] = name
            rows[identity] = row

        # A removed song stays removed. Remove all of its original staged state,
        # including original manual conversions which no longer have a row.
        for identity, original in originals.items():
            if identity not in latest:
                path = _path(original)
                for field in _SONG_MAPS:
                    merged.get(field, {}).pop(path, None)
                if image_mode and path not in baseline.get("pendingImageAdditions", {}):
                    merged.setdefault("pendingImageDeletes", set()).add(path)

        common = set(prepared) & set(latest)
        reordered = [key for key in latest if key in common] != [key for key in prepared if key in common]
        order = list(latest) if reordered else list(originals) + [key for key in latest if key not in originals]
        result_rows = [rows[key] for key in order if key in rows]

        # Additions have no immutable image entry: their key is their filename.
        for row in result_rows:
            path = _path(row)
            target = merged.get("pendingImageRenames", {}).get(path)
            if image_mode and path in merged.get("pendingImageAdditions", {}) and target:
                merged = _remap_state(merged, {path: target})
                merged.get("pendingImageRenames", {}).pop(target, None)
                row[1].setText(target)

        result = dict(current)
        result.update(state=merged, rows=result_rows, copies={}, preparation_layer=None)
        for field in ("album", "catalog"):
            result[field] = self.before[field] if current[field] == self.after[field] else current[field]
        return result
