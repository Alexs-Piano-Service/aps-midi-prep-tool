# Startup configuration for USB deployments

Place a UTF-8 file named **`aps-midi-prep-tool.json`** in the same directory as
APS MIDI Prep Tool's executable. APS reads it automatically at every launch,
before displaying its main window or welcome dialog. This lets you prepare a
USB stick for a particular instrument with the right folders and preferences.

- **Windows:** beside `APSMidiPrepTool.exe`, including a standalone one-file build.
- **Linux AppImage:** beside the `.AppImage` file, outside the AppImage.
- **Other packaged builds:** beside the executable.
- **Running Python source:** beside `aps_midi_prep_tool.py`.

The launcher's working directory and temporary extraction directories are not
searched. The configuration filename stays the same if you rename the executable.

## A Nalbantov USB stick

Copy the [example configuration](examples/aps-midi-prep-tool.json) beside APS
and edit it for the recipient's instrument. For example:

```text
USB stick/
  APSMidiPrepTool.exe
  aps-midi-prep-tool.json
  Music/
  Nalbantov/
  Prepared/
```

A minimal configuration for a Mark II with Nalbantov is:

```json
{
  "preparation_profile": "mark_ii",
  "preparation_medium": "nalbantov",
  "open_folder_location": "./Music",
  "open_image_location": "./Nalbantov",
  "emulator_image_source": "./Music",
  "emulator_image_output": "./Nalbantov",
  "skip_first_time_dialog": true
}
```

This selects Mark II preparation, E-SEQ songs, 720 KB HFE images, and disk names
starting at `DSKA0000.HFE`. Opening a folder, importing songs, or starting a build
still uses the usual APS controls; the configuration does not automatically
open, convert, or write any music files.

Paths such as `./Music`, `./Nalbantov`, `.` and `../Music` are relative to the
configuration file, so moving the stick to a different drive letter or mount
point works. Use forward slashes on Windows too. Absolute paths are supported
for the current operating system, and `~` expands to the current user's home.
An empty path keeps that dialog's normal fallback. Create source folders before
use; setting a path does not create or scan the directory. Open dialogs fall
back to an existing parent or their usual starting location if a folder is missing.

## How initial settings work

The file contains a single JSON object with the saved setting names below.
Use JSON `true`/`false` for booleans, whole numbers for integers, and quoted
strings for text and paths. JSON does not allow comments or trailing commas.
Windows editors that add a UTF-8 byte-order mark are supported.

On each launch, APS first migrates its previously saved preferences, then
applies the configuration. Explicit entries override saved preferences,
including remembered dialogs that an upgrade would otherwise reset. Settings
you omit retain their saved values, or the app's defaults on a new installation.
Choosing `preparation_profile` also applies that instrument's preparation
defaults, with `preparation_medium` selecting the delivery method. If you omit
the medium, APS uses the instrument's default. Other explicit entries override
those proposed defaults. The selected instrument's required song format and
delivery constraints still apply; choose the `custom` profile when you need
manual preparation.

You can change settings normally while APS runs. Changes are saved in APS's
usual per-user settings store on that computer; APS never rewrites the JSON
file. Its listed settings are reapplied the next time APS launches. Removing
the file stops applying it, but does not undo values already saved on that
computer. This is a deployment preset, rather than a separate portable settings
store. To change a deployment's initial choices permanently, edit the JSON file.

If the file cannot be read or contains malformed JSON, duplicate or unknown
keys, incorrect value types, or an invalid instrument/delivery pair, APS ignores
the entire file and displays an explanation. Correct it and restart APS. No
entries from an invalid file are applied. The file can be read-only.

## Instrument and delivery

| Setting | Values |
| --- | --- |
| `preparation_profile` | An instrument key from the table below. |
| `preparation_medium` | `original`, `nalbantov`, `flashfloppy_img`, `flashfloppy_hfe`, `emulator_custom`, `usb`, `pianodisc_app`, or `custom`, as supported by the instrument. Set it together with the profile for a reproducible deployment. |
| `preparation_image_format` | `img` or `hfe`; initial New Image / Save As Image type. |
| `preparation_disk_format` | `ibm.720` or `ibm.1440`; initial New Image / Save As Image capacity. |

| Instrument key | Instrument / purpose | Default delivery |
| --- | --- | --- |
| `unsure` | I'm not sure | `custom` |
| `custom` | Custom / manual preparation | `custom` |
| `mark_i` | Mark I | `original` |
| `mark_ii` | Mark II | `original` |
| `dsr1` | DSR1 upgrade | `original` |
| `mark_ii_xg` | Mark II XG | `original` |
| `mark_iii` | Mark III | `original` |
| `mark_iv` | Mark IV / PRO | `usb` |
| `e3_850` | E3 / DKC-800 / DKC-850 | `usb` |
| `enspire` | ENSPIRE / DKC-900 | `usb` |
| `midi_export` | Standard MIDI export | `usb` |
| `pianodisc_128plus` | PDS-128 Plus | `original` |
| `pianodisc_228cfx` | PDS-228CFX / SilentDrive | `original` |
| `pianodisc_prodigy` | Prodigy / Prodigy II (iQ Player) | `pianodisc_app` |
| `qrs_chili` | Chili / AMC | `original` |
| `qrs_pmii` | PNOmation II / PMII | `usb` |
| `qrs_pno3` | PNO3 | `usb` |
| `qrs_pno4` | PNO4 / PNO4 Touch | `usb` |

Nalbantov and FlashFloppy are offered for the floppy-capable Disklavier profiles
(`mark_i` through `mark_iv` above, including `dsr1`). PianoDisc floppy models
and QRS Chili offer `original` and `emulator_custom`. USB-only profiles use
`usb`; Prodigy uses `pianodisc_app`. `custom` keeps manual settings. See
[preparation profiles](preparation-profiles.md) for controller behavior.

## Remembered paths

Every entry in this table accepts an absolute or relative path string.

| Setting | Purpose |
| --- | --- |
| `open_folder_location` | Starting folder for Open MIDI Folder; remembers subsequent folder selections. |
| `open_image_location` | Starting folder for Open Image; remembers the selected image's folder. |
| `save_as_location` | Shared starting folder for Save As dialogs. |
| `bulk_extraction_source` | Bulk Extraction source folder. |
| `bulk_extraction_output` | Bulk Extraction output folder. |
| `bulk_extraction_last_job` | Suggested extraction job record file when resuming. |
| `emulator_image_source` | Build Emulator Disk Set source folder. |
| `emulator_image_output` | Build Emulator Disk Set output folder, such as `./Nalbantov`. |
| `disk_recovery_image_path` | Initial image path in Recover Damaged Image. |

## Appearance, interface, and updates

| Setting | Type / purpose |
| --- | --- |
| `language` | String: `en`, `es`, `fr`, `de`, `it`, `pt-BR`, `bg`, `nl`, `pl`, `ja`, `ko`, `zh-Hans`. |
| `appearance_mode` | String: `system`, `light`, `dark`. |
| `font_scale` | String: `regular`, `small`, `compact`. |
| `hide_status` | Boolean: hide the status area. |
| `hide_quick_panel` | Boolean: hide the quick controls. |
| `hide_album_metadata` | Boolean: hide album metadata controls. |
| `show_save_destination` | Boolean: show the save location. |
| `show_preparation_row` | Boolean: show the active preparation row. |
| `show_compat_warning` | Boolean: show title compatibility warnings. |
| `format_disklavier_screen` | Boolean: format the title display for the Disklavier screen. |
| `check_updates_at_startup` | Boolean: check for updates automatically. |
| `skip_update_reminders` | Boolean: suppress update reminders. Set this to `true` and `check_updates_at_startup` to `false` for an offline deployment. |

## Remembered dialog choices

The review invitation can also be configured:

| Setting | Type / purpose |
| --- | --- |
| `successful_disk_reads` | Integer: successful physical reads remembered across launches (initially `0`). Image opens and conversions do not count. |
| `review_prompt_after_reads` | Integer: total successful reads required before the next invitation (initially `3`). Remind me later raises this by three from the current count. |
| `never_ask_for_review` | Boolean: suppress review invitations. This is also set when choosing Never ask again or successfully opening the review page. |

All entries below are booleans except `eseq_to_midi_switch_mode`.

| Setting | Meaning when `true` |
| --- | --- |
| `skip_first_time_dialog` | Skip the welcome screen; Help can still open it. |
| `skip_type0_warning` | Skip the remembered Type 0 conversion warning. |
| `skip_image_remove_warning` | Skip the remembered image removal warning. |
| `skip_image_delete_on_save_warning` | Skip the remembered image deletion-on-save warning. |
| `skip_floppy_write_warning` | Skip the remembered floppy write warning. |
| `hide_recovery_complete_dialog` | Hide the recovery completion dialog. |
| `hide_save_as_image_complete_dialog` | Hide the Save As Image completion dialog. |
| `skip_eseq_to_midi_conversion_prompt` | Use remembered E-SEQ-to-MIDI choices without showing that options prompt. |
| `hide_gw_sector_report_read_v1` | Hide Greaseweazle read sector reports. |
| `hide_gw_sector_report_write_v1` | Hide Greaseweazle write sector reports. |
| `hide_gw_sector_report_convert_v1` | Hide Greaseweazle conversion sector reports. |
| `hide_gw_sector_report_recover_v1` | Hide Greaseweazle recovery sector reports. |

`eseq_to_midi_switch_mode` accepts `"ask"` or `"switch"` (`"export"` is a legacy
alias for `"switch"`). This remembers the choice to export converted MIDI and
leave image mode. A destination is still selected through the usual dialog.

## Saving and filename preferences

| Setting | Meaning when `true` (all booleans) |
| --- | --- |
| `store_backups` | Back up before saving. |
| `use_dos83_filenames` | Use DOS 8.3 filenames for new changes; takes precedence over descriptive filenames. |
| `long_midi_filenames` | Use descriptive MIDI export filenames. |
| `eseq_to_midi_long_filenames` | Legacy per-dialog descriptive filename preference. |
| `read_floppy_long_filenames` | Legacy per-dialog descriptive filename preference. |
| `eseq_to_midi_trim_title_spaces` | Trim title spaces during E-SEQ-to-MIDI conversion. |
| `eseq_export_album_subfolder` | Export E-SEQ songs into an album subfolder. |
| `image_export_album_subfolder` | Export disk images into an album subfolder. |
| `write_tag_sidecars` | Write tag sidecar files. |
| `write_metadata_summary` | Write a metadata summary. |
| `auto_write_protect_on_load` | Automatically protect originals when loaded. |
| `allow_floppy_save` | Remember permission to save to the original floppy. |
| `confirm_image_save` | Remember permission to save to the original image. |
| `verify_floppy_after_write` | Read back a floppy after writing to verify it. |

Prefer `long_midi_filenames` when making a new file. The two legacy filename
keys remain supported for compatibility; when the current key exists, it
controls the shared choice. Existing save/format/write confirmations and
destination constraints continue to work as they do for settings chosen in APS.

## Bulk extraction

These are booleans; source, output, and job paths are listed above.

| Setting | Meaning when `true` |
| --- | --- |
| `bulk_extraction_convert_eseq` | Convert extracted E-SEQ songs to MIDI. |
| `bulk_extraction_long_midi_filenames` | Use descriptive MIDI filenames. |
| `bulk_extraction_trim_title_spaces` | Trim title spaces. |
| `bulk_extraction_include_eseq_sources` | Include original E-SEQ sources alongside converted songs. |
| `bulk_extraction_use_album_names` | Use album names for output folders. |

## Emulator disk sets

| Setting | Type / values |
| --- | --- |
| `emulator_image_prefix` | String, such as `DSKA`. |
| `emulator_image_starting_number` | Integer: first disk number (normally `0`). |
| `emulator_image_safety_margin_kib` | Integer: reserved capacity in KiB (normally `32`). |
| `emulator_image_album_title_override` | String: album title override, or `""`. |
| `emulator_image_content` | String: `eseq` or `midi`; the active instrument's required format takes precedence. |
| `emulator_image_output_format` | String: `hfe` or `img`; the delivery method may fix this. |
| `emulator_image_disk_format` | String: `ibm.720` or `ibm.1440`. |
| `emulator_image_disk_layout` | String: `folders` for one album per folder, or `fill` for automatic filling. |
| `emulator_image_include_subfolders` | Boolean: scan nested folders in automatic-fill mode; folder-album layout always includes them. |
| `emulator_image_shuffle` | Boolean: shuffle songs. |
| `emulator_image_include_song_lists` | Boolean: include song lists. |

## Floppy reading and recovery

| Setting | Type / values |
| --- | --- |
| `greaseweazle_device_path` | String: device identifier such as `COM3` or `/dev/ttyACM0`; never resolved as a relative file path. |
| `greaseweazle_drive` | String: `A`, `B`, `0`, `1`, or `2`. |
| `read_floppy_source_kind` | String: the remembered source kind, `floppy_usb` or `floppy_gw`. |
| `read_floppy_gw_archival` | Boolean: legacy archival SCP capture preference. |
| `read_floppy_gw_image_type` | String: preferred retained capture type, such as `none`, `img`, `hfe`, or `scp`. |
| `image_floppy_drive_image_type` | String: preferred direct-drive Image Floppy output type: `img`, `hfe`, `bin`, or `ima`. |
| `read_floppy_gw_format` | String: disk format key, normally `ibm.720` or `ibm.1440`. |
| `read_floppy_gw_revs` | Integer: capture revolutions, `0` through `20`. |
| `read_floppy_gw_retries` | Integer: retries, `0` through `20`. |
| `read_floppy_convert_to_midi` | Boolean: convert recovered E-SEQ to MIDI when the preparation destination permits it. |
| `read_floppy_start_recovery` | Boolean: start with the recovery workflow selected. |
| `read_floppy_trim_titles` | Boolean: trim recovered titles. |
| `disk_recovery_image_format` | String: `autodetect` or a supported disk format key. |
| `disk_recovery_floppy_format` | String: disk format key for recovery from a physical floppy. |

Numeric dialog controls apply their usual limits. Optional hardware/tool choices
are available only when the corresponding device or tool is available.

## Keyboard shortcuts and migration markers

Shortcut keys use `keyboard_shortcuts/<action-id>` with a string in Qt portable
shortcut notation. For example:

```json
{
  "keyboard_shortcuts/file.open_folder": "Ctrl+O",
  "keyboard_shortcuts/file.save": "Alt+S",
  "keyboard_shortcuts/help.welcome": ""
}
```

An empty string disables that shortcut. Action IDs are listed in
`MidiTitleWindow._keyboard_shortcut_specs()` in
[`main_window.py`](../aps_midi_prep_tool_app/main_window.py); use Settings →
Keyboard Shortcuts to review the resulting assignments.

For completeness, the saved integer migration markers
`hide_choices_reset_version` and `gw_sector_report_hide_version` are accepted.
Normally omit them: configured dialog choices already apply after migrations,
so a new USB deployment does not need internal version numbers.
