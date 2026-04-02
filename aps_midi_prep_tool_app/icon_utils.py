import sys
from functools import lru_cache
from pathlib import Path

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget


def _icon_file_names() -> tuple[str, ...]:
    if sys.platform.startswith("win"):
        return ("aps.ico", "aps.png")
    if sys.platform == "darwin":
        return ("aps.icns", "aps.ico", "aps.png")
    return ("aps.ico", "aps.png")


def _icon_roots() -> list[Path]:
    roots = [Path(__file__).resolve().parent]
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        bundle_root_path = Path(bundle_root)
        roots.append(bundle_root_path / "aps_midi_prep_tool_app")
        roots.append(bundle_root_path)
    return roots


def _icon_candidates() -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in _icon_roots():
        for file_name in _icon_file_names():
            candidate = root / file_name
            if candidate in seen:
                continue
            seen.add(candidate)
            candidates.append(candidate)
    return candidates


@lru_cache(maxsize=1)
def load_app_icon() -> QIcon:
    for icon_path in _icon_candidates():
        if not icon_path.is_file():
            continue
        icon = QIcon(str(icon_path))
        if not icon.isNull():
            return icon
    return QIcon()


def apply_window_icon(widget: QWidget) -> None:
    icon = load_app_icon()
    if icon.isNull():
        return
    widget.setWindowIcon(icon)
