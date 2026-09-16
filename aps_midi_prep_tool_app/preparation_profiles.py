"""Documented destination defaults, separate from emulator configuration.

These immutable profiles describe the defaults and required song format used
by the reviewable preparation workflow. No profile claims hardware testing.
"""

from dataclasses import dataclass


COMPATIBILITY_SOURCE = "https://www.alexanderpeppe.com/disklavier-compatibility-table/"
COMPATIBILITY_REVIEWED = "2026-09-07"
FLASHFLOPPY_SOURCE = "https://github.com/keirf/flashfloppy/wiki/Image-Navigation-Modes"
SETTING_PROFILE = "preparation_profile"
SETTING_MEDIUM = "preparation_medium"
SETTING_IMAGE_FORMAT = "preparation_image_format"
SETTING_DISK_FORMAT = "preparation_disk_format"
PIANO_PROFILE_CATEGORIES = (
    ("general", "General"),
    ("disklavier", "Disklavier"),
    ("pianodisc", "PianoDisc"),
    ("qrs", "QRS"),
)
MANUFACTURER_COMPATIBILITY_REVIEWED = "2026-09-08"
PIANODISC_128PLUS_SOURCE = "https://www.intunepianoservices.com/PianoDisc%20PDS128%20Plus%20Owner%27s%20Manual.pdf"
PIANODISC_228CFX_SOURCE = "https://pianodisc.com/wp-content/uploads/UserGuides/Silent%20Drive%20%28PDS-228%20CFX%29%20-%20User%20Guide.pdf"
PIANODISC_PRODIGY_SOURCE = "https://pianodisc.com/wp-content/uploads/UserGuides/Prodigy%20User%20Guide.pdf"
QRS_PMII_SOURCE = "https://www.qrsmusic.com/assets/pdf/QRS%20PNOmation%20II%20User%20Guide%20and%20Upgrade%20Instructions_V553%20Rev%2020150909.pdf"
QRS_PNO3_SOURCE = "https://www.qrsmusic.com/assets/pdf/2022%20PNO3%20Owners%20Manual.pdf"
QRS_PNO4_SOURCE = "https://www.qrsmusic.com/assets/pdf/PNO4%20Owners%20Manual.pdf"
QRS_CHILI_SOURCE = "https://www.qrsmusic.com/assets/pdf/im77500.pdf"
PIANODISC_FLOPPY_NOTE = "Use MIDI Type 0 on a 720 KB floppy. Type 1 files are prepared automatically; channels and instruments are preserved."
PIANODISC_PRODIGY_NOTE = "Export MIDI files, then import them into the PianoDisc iQ Player app for playback on Prodigy."
QRS_USB_NOTE = "Copy the exported MIDI files to a USB drive and select that drive on the controller. Keep the controller's piano playback settings."
QRS_FLOPPY_NOTE = "Use MIDI Type 0 or Type 1 on a DOS-formatted HD floppy. Keep the controller's piano playback settings."


@dataclass(frozen=True)
class PianoProfile:
    key: str
    label: str
    song_format: str | None = None
    supported_song_formats: tuple[str, ...] = ()
    # Empty means no documented SMF subtype claim. A MIDI profile with (0,)
    # requires staged Type 0 conversion; (0, 1) preserves either subtype.
    midi_types: tuple[int, ...] = ()
    disk_format_key: str | None = None
    media: tuple[str, ...] = ("custom",)
    default_medium: str = "custom"
    evidence_level: str = "unverified"
    caution: str = "Choose the controller model before using compatibility defaults."
    source_url: str = COMPATIBILITY_SOURCE
    category: str = "disklavier"
    source_label: str = "APS Disklavier Compatibility Table"
    preparation_note: str = ""


FLOPPY_MEDIA = ("original", "nalbantov", "flashfloppy_img", "flashfloppy_hfe", "emulator_custom")
PIANO_PROFILES = (
    PianoProfile(
        "unsure", "I'm not sure", category="general",
        media=("custom", "original", "flashfloppy_img", "flashfloppy_hfe", "emulator_custom", "usb"),
    ),
    PianoProfile("custom", "Custom", caution="Keep manual conversion settings; use descriptive filenames and standard title editing.", category="general"),
    PianoProfile(
        "mark_i", "Mark I — MX100A/B, DKW10, DKC5R", "eseq", ("eseq",), (),
        "ibm.720", FLOPPY_MEDIA, "original", "documented",
        "Use E-SEQ on 720 KB (2DD) disks. MIDI IN support does not imply MIDI-file playback from floppy.",
    ),
    PianoProfile(
        "mark_ii", "Mark II — MX100II / MPX100II / HQ100 / DKC100R", "eseq", ("eseq",), (),
        "ibm.720", FLOPPY_MEDIA, "original", "documented",
        "E-SEQ on 720 KB (2DD) is the conservative choice. SMF0 playback varies by unit or firmware; confirm it before choosing MIDI manually.",
    ),
    PianoProfile(
        "dsr1", "DSR1 upgrade", "midi", ("midi", "eseq"), (0, 1),
        "ibm.1440", FLOPPY_MEDIA, "original", "documented",
        "The DSR1 controller supports MIDI and E-SEQ on 2DD or 2HD disks. Keep one song format per floppy.",
    ),
    PianoProfile(
        "mark_ii_xg", "Mark II XG — DKC100XG / DKC500 series / DKC50R", "midi", ("midi", "eseq"), (0, 1),
        "ibm.1440", FLOPPY_MEDIA, "original", "documented",
        "MIDI Type 0 and Type 1 are supported. Keep channels and instruments unless you explicitly want a piano-only arrangement.",
    ),
    PianoProfile(
        "mark_iii", "Mark III — DKC55 / DKC55CD / DKC55RCD / DKC60RCD", "midi", ("midi", "eseq"), (0, 1),
        "ibm.1440", FLOPPY_MEDIA, "original", "documented",
        "MIDI and E-SEQ playback are documented. Keep one song format per floppy; recording features vary by controller.",
    ),
    PianoProfile(
        "mark_iv", "Mark IV / PRO", "midi", ("midi", "eseq"), (0, 1),
        "ibm.1440", ("usb",) + FLOPPY_MEDIA, "usb", "documented",
        "MIDI folder export is preferred for USB. E-SEQ playback is supported; preserve PRO/XP metadata unless a separate conversion requires changes.",
    ),
    PianoProfile(
        "e3_850", "E3 / DKC-800 / DKC-850", "midi", ("midi", "eseq"), (0, 1),
        None, ("usb",), "usb", "documented",
        "Use MIDI folder export for USB. E-SEQ playback is supported; DKC-850 recording capabilities depend on the host piano.",
    ),
    PianoProfile(
        "enspire", "ENSPIRE / DKC-900", "midi", ("midi",), (0, 1),
        None, ("usb",), "usb", "documented",
        "Use MIDI folder export for USB. E-SEQ songs are prepared as MIDI before delivery; E-SEQ playback is not supported.",
    ),
    PianoProfile(
        "midi_export", "Standard MIDI export / modern playback", "midi", ("midi",), (0, 1),
        None, ("usb",), "usb", "unverified",
        "Preserve MIDI channels, instruments, pedals, and metadata. Check the destination player's own requirements.",
        "",
        category="general",
    ),
    PianoProfile(
        "pianodisc_128plus", "PDS-128 Plus", "midi", ("midi",), (0,),
        "ibm.720", ("original", "emulator_custom"), "original", "documented",
        PIANODISC_FLOPPY_NOTE, PIANODISC_128PLUS_SOURCE, category="pianodisc",
        source_label="PianoDisc user manual", preparation_note=PIANODISC_FLOPPY_NOTE,
    ),
    PianoProfile(
        "pianodisc_228cfx", "PDS-228CFX / SilentDrive", "midi", ("midi",), (0,),
        "ibm.720", ("original", "emulator_custom"), "original", "documented",
        PIANODISC_FLOPPY_NOTE, PIANODISC_228CFX_SOURCE, category="pianodisc",
        source_label="PianoDisc user manual", preparation_note=PIANODISC_FLOPPY_NOTE,
    ),
    PianoProfile(
        "pianodisc_prodigy", "Prodigy / Prodigy II (iQ Player)", "midi", ("midi",), (),
        None, ("pianodisc_app",), "pianodisc_app", "documented",
        PIANODISC_PRODIGY_NOTE, PIANODISC_PRODIGY_SOURCE, category="pianodisc",
        source_label="PianoDisc user manual", preparation_note=PIANODISC_PRODIGY_NOTE,
    ),
    PianoProfile(
        "qrs_chili", "Chili / AMC", "midi", ("midi",), (0, 1),
        "ibm.1440", ("original", "emulator_custom"), "original", "documented",
        QRS_FLOPPY_NOTE, QRS_CHILI_SOURCE, category="qrs",
        source_label="QRS user manual", preparation_note=QRS_FLOPPY_NOTE,
    ),
    PianoProfile(
        "qrs_pmii", "PNOmation II / PMII", "midi", ("midi",), (),
        None, ("usb",), "usb", "documented",
        QRS_USB_NOTE, QRS_PMII_SOURCE, category="qrs",
        source_label="QRS user manual", preparation_note=QRS_USB_NOTE,
    ),
    PianoProfile(
        "qrs_pno3", "PNO3", "midi", ("midi",), (),
        None, ("usb",), "usb", "documented",
        QRS_USB_NOTE, QRS_PNO3_SOURCE, category="qrs",
        source_label="QRS user manual", preparation_note=QRS_USB_NOTE,
    ),
    PianoProfile(
        "qrs_pno4", "PNO4 / PNO4 Touch", "midi", ("midi",), (),
        None, ("usb",), "usb", "documented",
        QRS_USB_NOTE, QRS_PNO4_SOURCE, category="qrs",
        source_label="QRS user manual", preparation_note=QRS_USB_NOTE,
    ),
)
PROFILES_BY_KEY = {profile.key: profile for profile in PIANO_PROFILES}


@dataclass(frozen=True)
class PreparationMedium:
    key: str
    label: str
    image_format: str | None = None
    image_prefix: str | None = None
    starting_number: int | None = None
    evidence_level: str = "unverified"
    caution: str = ""
    source_url: str = ""


MEDIA = (
    PreparationMedium("custom", "Keep current settings"),
    PreparationMedium("original", "Original floppy drive", "img", evidence_level="documented"),
    PreparationMedium(
        "nalbantov", "Nalbantov", "hfe", "DSKA", 0, "documented",
        source_url="https://www.alexanderpeppe.com/copying-a-yamaha-pianosoft-floppy-disk-to-a-nalbantov-usb-stick/",
    ),
    PreparationMedium(
        "flashfloppy_img", "FlashFloppy — IMG", "img", "DSKA", 0, "documented",
        "Requires FlashFloppy indexed mode and a matching drive interface. Disk-set names start at DSKA0000.IMG.",
        FLASHFLOPPY_SOURCE,
    ),
    PreparationMedium(
        "flashfloppy_hfe", "FlashFloppy — HFE", "hfe", "DSKA", 0, "documented",
        "Requires FlashFloppy indexed mode and a matching drive interface. Disk-set names start at DSKA0000.HFE.",
        FLASHFLOPPY_SOURCE,
    ),
    PreparationMedium(
        "emulator_custom", "Other emulator / manual configuration", caution=
        "Confirm image type, naming, numbering, and drive configuration with the emulator documentation. Existing image settings are kept.",
    ),
    PreparationMedium("usb", "USB / folder export", evidence_level="documented"),
    PreparationMedium("pianodisc_app", "PianoDisc iQ app / folder export", evidence_level="documented"),
)
MEDIA_BY_KEY = {medium.key: medium for medium in MEDIA}


def get_preparation_profile(key):
    return PROFILES_BY_KEY.get(str(key or ""), PROFILES_BY_KEY["unsure"])


def get_preparation_medium(profile, key):
    key = str(key or "")
    # Older preferences used a separate name for the same HFE/DSKA workflow.
    if key == "nalbantov_slim":
        key = "nalbantov"
    if key not in profile.media:
        key = profile.default_medium
    return MEDIA_BY_KEY[key]


def proposed_settings(profile, medium):
    """Return only defaults the user can review before applying this profile."""
    medium = get_preparation_medium(profile, medium.key)
    floppy = medium.key in FLOPPY_MEDIA
    use_dos83 = floppy and profile.song_format is not None
    changes = {
        "use_dos83_filenames": use_dos83,
        "long_midi_filenames": not use_dos83,
        "eseq_to_midi_long_filenames": not use_dos83,
        "read_floppy_long_filenames": not use_dos83,
        "bulk_extraction_long_midi_filenames": not use_dos83,
        "format_disklavier_screen": profile.key in {"mark_i", "mark_ii", "mark_ii_xg", "mark_iii"},
    }
    if profile.song_format is not None:
        changes["emulator_image_content"] = profile.song_format
    if floppy and profile.disk_format_key:
        changes["emulator_image_disk_format"] = profile.disk_format_key
        changes[SETTING_DISK_FORMAT] = profile.disk_format_key
    if medium.image_format:
        changes["emulator_image_output_format"] = medium.image_format
        changes[SETTING_IMAGE_FORMAT] = medium.image_format
    if medium.image_prefix is not None:
        changes["emulator_image_prefix"] = medium.image_prefix
    if medium.starting_number is not None:
        changes["emulator_image_starting_number"] = medium.starting_number
    return changes


SETTING_LABELS = {
    "emulator_image_content": "Disk-set song format",
    "use_dos83_filenames": "Use 8.3 filenames for new changes",
    "long_midi_filenames": "Descriptive MIDI export filenames",
    "eseq_to_midi_long_filenames": "Descriptive E-SEQ to MIDI filenames",
    "read_floppy_long_filenames": "Descriptive floppy MIDI export filenames",
    "bulk_extraction_long_midi_filenames": "Descriptive MIDI export filenames",
    "format_disklavier_screen": "Format for Disklavier screen",
    "emulator_image_disk_format": "Disk-set capacity",
    SETTING_DISK_FORMAT: "New image / Save As Image capacity",
    "emulator_image_output_format": "Disk-set image type",
    SETTING_IMAGE_FORMAT: "New image / Save As Image type",
    "emulator_image_prefix": "Emulator disk-set prefix",
    "emulator_image_starting_number": "First emulator disk number",
}

PREPARATION_SETTING_KEYS = (SETTING_PROFILE, SETTING_MEDIUM, *SETTING_LABELS)


def display_setting(value):
    if isinstance(value, bool):
        return "On" if value else "Off"
    return {
        "ibm.720": "720 KB (2DD)", "ibm.1440": "1.44 MB (2HD)",
        "midi": "MIDI", "eseq": "Yamaha E-SEQ + PIANODIR.FIL",
        "img": "IMG", "hfe": "HFE", "": "Current default",
    }.get(str(value), str(value))
