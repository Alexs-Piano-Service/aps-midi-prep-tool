"""Translate backup reports at display time without changing saved diagnostics."""

import re
from string import Formatter

from .markiv_backup_diagnostics_translations import MARKIV_BACKUP_DIAGNOSTICS_TRANSLATIONS
from .message_catalog import translate_text


def _diagnostic_patterns():
    templates = (*MARKIV_BACKUP_DIAGNOSTICS_TRANSLATIONS,
                 "Drive detection did not finish within {seconds} seconds.")
    for template in templates:
        fields = []
        pattern = ""
        for literal, field, _spec, _conversion in Formatter().parse(template):
            pattern += re.escape(literal)
            if field:
                fields.append(field)
                pattern += "(.+?)"
        yield template, fields, re.compile(r"(.*?: )?" + pattern, re.DOTALL)


_PATTERNS = tuple(_diagnostic_patterns())


def localize_backup_diagnostic(message, language_code=None):
    """Preserve path prefixes and unknown OS details while translating guidance."""
    message = str(message)
    matches = [(template, fields, match) for template, fields, pattern in _PATTERNS
               if (match := pattern.fullmatch(message)) is not None]
    if matches:
        # A wrapper must win over its nested diagnostic, otherwise its English
        # wording would be mistaken for part of the filename prefix.
        template, fields, match = min(matches, key=lambda candidate: len(candidate[2][1] or ""))
        values = dict(zip(fields, match.groups()[1:]))
        if "error" in values:
            values["error"] = localize_backup_diagnostic(values["error"], language_code)
        if "status" in values:
            status = {
                "complete": "Complete", "cancelled": "Cancelled",
                "incomplete": "Incomplete", "in_progress": "In progress",
                "unknown": "Unknown",
            }.get(values["status"], values["status"])
            values["status"] = translate_text(status, language_code)
        return (match[1] or "") + translate_text(template, language_code, **values)
    return translate_text(message, language_code)
