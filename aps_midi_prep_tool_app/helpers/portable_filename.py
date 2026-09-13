"""Shared Windows device-name rules for portable output filenames."""

_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "123456789¹²³"),
}


def is_windows_device_name(name):
    """Check a filename component, including device names with extensions.

    Windows treats superscript ¹, ², and ³ as device-number digits too:
    https://learn.microsoft.com/en-us/windows/win32/fileio/naming-a-file
    """
    return name.split(".", 1)[0].rstrip(" ").upper() in _WINDOWS_RESERVED_NAMES
