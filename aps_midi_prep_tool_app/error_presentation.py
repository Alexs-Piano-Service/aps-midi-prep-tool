"""Keep error summaries readable without discarding diagnostic information."""

import re


def brief_error_detail(detail, *, max_chars=240, max_lines=3):
    """Return a short explanatory paragraph, never a log or a cut-off path."""
    text = str(detail or "").strip()
    paragraph = text.split("\n\n", 1)[0].strip()
    if (len(paragraph) > max_chars or len(paragraph.splitlines()) > max_lines
            or re.search(r"Traceback|(?:^|\n)\s*(?:Stage:|File \"|\w*(?:Error|Exception):)", paragraph)
            or any(len(word) > 100 for word in paragraph.split())):
        return ""
    return paragraph
