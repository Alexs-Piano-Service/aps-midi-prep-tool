"""Translate recognized write diagnostics without altering paths or saved logs."""

import re
from string import Formatter

from .conversion_review import localize_music_error
from .message_catalog import translate_text
from .write_safety_translations import WRITE_SAFETY_TRANSLATIONS


_FILE_PROGRESS_TEMPLATE = "{action}\nFile {index} of {total}: {name}"


def _message_patterns():
    for template in WRITE_SAFETY_TRANSLATIONS:
        parts = []
        fields = []
        for literal, field, _spec, _conversion in Formatter().parse(template):
            parts.append(re.escape(literal))
            if field:
                fields.append(field)
                if field in {"needed", "available", "index", "total"}:
                    parts.append(r"(\d+)")
                elif template == _FILE_PROGRESS_TEMPLATE and field == "action":
                    parts.append(r"([^\n]+)")
                else:
                    parts.append(r"(.+?)")
        if fields:
            yield template, fields, re.compile("".join(parts), re.DOTALL)


_PATTERNS = tuple(_message_patterns())


def localize_write_message(message, language_code=None):
    """Translate complete app messages, preserving inserted device and file data."""
    message = str(message)
    if message in WRITE_SAFETY_TRANSLATIONS:
        return translate_text(message, language_code)
    for template, fields, pattern in _PATTERNS:
        match = pattern.fullmatch(message)
        if match is None:
            continue
        values = dict(zip(fields, match.groups(), strict=True))
        if template == _FILE_PROGRESS_TEMPLATE:
            # Only the app-supplied action is another message. The filename can
            # contain newlines, braces, or text identical to a catalog entry.
            values["action"] = localize_write_message(values["action"], language_code)
        if "error" in values:
            values["error"] = localize_write_message(values["error"], language_code)
        return translate_text(template, language_code, **values)
    return localize_music_error(message, language_code)
