"""Read deployment settings beside the executable without modifying the file."""

import json
import os
from pathlib import Path
import sys

from .preparation_profiles import (
    FLOPPY_MEDIA, MEDIA_BY_KEY, PROFILES_BY_KEY,
    SETTING_DISK_FORMAT, SETTING_IMAGE_FORMAT, SETTING_MEDIUM, SETTING_PROFILE,
    get_preparation_medium, get_preparation_profile, proposed_settings,
)


CONFIG_FILENAME = "aps-midi-prep-tool.json"

# Paths to user files/directories, excluding hardware identifiers such as COM3.
PATH_SETTINGS = frozenset({
    "open_folder_location", "open_image_location", "save_as_location",
    "bulk_extraction_source", "bulk_extraction_output", "bulk_extraction_last_job",
    "emulator_image_source", "emulator_image_output", "disk_recovery_image_path",
    "markiv_backup_source", "markiv_backup_output", "markiv_backup_last_folder",
})
BOOLEAN_SETTINGS = frozenset({
    "show_compat_warning", "store_backups", "use_dos83_filenames", "hide_status",
    "hide_quick_panel", "hide_album_metadata", "show_save_destination",
    "show_preparation_row", "skip_first_time_dialog", "skip_type0_warning",
    "skip_image_remove_warning", "skip_image_delete_on_save_warning",
    "skip_floppy_write_warning", "hide_recovery_complete_dialog",
    "hide_save_as_image_complete_dialog", "skip_eseq_to_midi_conversion_prompt",
    "long_midi_filenames", "eseq_to_midi_long_filenames",
    "eseq_to_midi_trim_title_spaces", "allow_floppy_save", "confirm_image_save",
    "auto_write_protect_on_load", "format_disklavier_screen",
    "eseq_export_album_subfolder", "image_export_album_subfolder",
    "read_floppy_gw_archival", "read_floppy_convert_to_midi",
    "read_floppy_long_filenames", "read_floppy_start_recovery",
    "read_floppy_trim_titles", "bulk_extraction_convert_eseq",
    "bulk_extraction_long_midi_filenames", "bulk_extraction_trim_title_spaces",
    "bulk_extraction_include_eseq_sources", "bulk_extraction_use_album_names",
    "emulator_image_include_subfolders", "emulator_image_shuffle",
    "emulator_image_include_song_lists", "check_updates_at_startup",
    "skip_update_reminders", "write_tag_sidecars", "write_metadata_summary",
    "verify_floppy_after_write", "hide_gw_sector_report_read_v1",
    "hide_gw_sector_report_write_v1", "hide_gw_sector_report_convert_v1",
    "hide_gw_sector_report_recover_v1",
    "never_ask_for_review",
    "markiv_backup_convert_eseq", "markiv_backup_keep_originals",
})
INTEGER_SETTINGS = frozenset({
    "read_floppy_gw_revs", "read_floppy_gw_retries",
    "emulator_image_starting_number", "emulator_image_safety_margin_kib",
    "hide_choices_reset_version", "gw_sector_report_hide_version",
    "filename_defaults_version",
    "successful_disk_reads", "review_prompt_after_reads",
})
STRING_SETTINGS = PATH_SETTINGS | frozenset({
    "language", "appearance_mode", "font_scale", "eseq_to_midi_switch_mode",
    "greaseweazle_device_path", "greaseweazle_drive", "read_floppy_source_kind",
    "read_floppy_gw_image_type", "image_floppy_drive_image_type",
    "read_floppy_gw_format", "disk_recovery_image_format",
    "disk_recovery_floppy_format", "emulator_image_prefix",
    "emulator_image_album_title_override", "emulator_image_content",
    "emulator_image_output_format", "emulator_image_disk_format",
    "emulator_image_disk_layout", SETTING_PROFILE, SETTING_MEDIUM,
    SETTING_IMAGE_FORMAT, SETTING_DISK_FORMAT,
})


class StartupConfigError(ValueError):
    def __init__(self, path, detail):
        self.path = Path(path)
        self.detail = str(detail)
        super().__init__(f"{self.path}: {self.detail}")


def startup_config_path():
    """Use the external AppImage/EXE location, never a bundle extraction dir."""
    if sys.platform.startswith("linux") and os.environ.get("APPIMAGE"):
        executable = Path(os.environ["APPIMAGE"])
    elif getattr(sys, "frozen", False):
        executable = Path(sys.executable)
    else:
        executable = Path(__file__).resolve().parent.parent / "aps_midi_prep_tool.py"
    return executable.absolute().parent / CONFIG_FILENAME


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate setting: {key}")
        result[key] = value
    return result


def load_startup_config(path=None):
    """Validate the whole UTF-8 JSON object before any saved settings change."""
    path = Path(path) if path is not None else startup_config_path()
    try:
        contents = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeError) as exc:
        raise StartupConfigError(path, exc) from exc
    try:
        values = json.loads(contents, object_pairs_hook=_unique_object)
        if not isinstance(values, dict):
            raise ValueError("Expected a JSON object containing setting names and values.")
        for key, value in values.items():
            if key in BOOLEAN_SETTINGS:
                expected = bool
            elif key in INTEGER_SETTINGS:
                expected = int
            elif key in STRING_SETTINGS or (
                key.startswith("keyboard_shortcuts/")
                and key.count("/") == 1 and key.split("/")[1]
            ):
                expected = str
            else:
                raise ValueError(f"Unknown setting: {key}")
            if type(value) is not expected:
                raise ValueError(f"{key}: expected {expected.__name__}.")
            if isinstance(value, str) and "\0" in value:
                raise ValueError(f"{key}: text cannot contain a null character.")
            if expected is int and not 0 <= value <= 2147483647:
                raise ValueError(f"{key}: expected an integer from 0 to 2147483647.")
        if SETTING_PROFILE in values and values[SETTING_PROFILE] not in PROFILES_BY_KEY:
            raise ValueError(f"Unknown preparation_profile: {values[SETTING_PROFILE]}")
        if SETTING_MEDIUM in values:
            medium_key = values[SETTING_MEDIUM]
            if medium_key not in MEDIA_BY_KEY:
                raise ValueError(f"Unknown preparation_medium: {medium_key}")
            if SETTING_PROFILE in values:
                profile = PROFILES_BY_KEY[values[SETTING_PROFILE]]
                if medium_key not in profile.media:
                    raise ValueError(f"preparation_medium {medium_key} is unavailable for {profile.key}.")
        for key in PATH_SETTINGS & values.keys():
            value = values[key]
            if value:
                # Forward slashes work on both supported platforms; accept
                # Windows-style relative USB paths on Linux as well.
                value = os.path.expanduser(value.replace("\\", "/"))
                values[key] = os.path.normpath(os.path.join(str(path.absolute().parent), value))
        return values
    except (ValueError, RecursionError) as exc:
        raise StartupConfigError(path, exc) from exc


def apply_startup_config(settings, values):
    """Apply after migrations, before widgets or onboarding read preferences.

    Explicit entries override the selected profile's defaults. Omitted entries
    retain the existing preference, except defaults supplied by a new profile.
    """
    if not values:
        return
    if SETTING_PROFILE in values or SETTING_MEDIUM in values:
        profile = get_preparation_profile(
            values.get(SETTING_PROFILE, settings.value(SETTING_PROFILE, "unsure"))
        )
        # A configured instrument must not inherit another deployment's medium.
        medium = get_preparation_medium(profile, values.get(SETTING_MEDIUM, profile.default_medium))
        for key, value in proposed_settings(profile, medium).items():
            settings.setValue(key, value)
        settings.setValue(SETTING_MEDIUM, medium.key)
        if profile.song_format and medium.key not in FLOPPY_MEDIA:
            settings.remove(SETTING_IMAGE_FORMAT)
            settings.remove(SETTING_DISK_FORMAT)
    for key, value in values.items():
        settings.setValue(key, value)
    settings.sync()
