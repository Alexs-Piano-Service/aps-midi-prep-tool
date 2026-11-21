"""Destination defaults for synchronized piano and accompaniment delivery.

These describe a playback route, not hardware certification. Keeping them
separate from MIDI/floppy preparation avoids treating an encoded audio file as
an ordinary USB song on controllers whose decoder lives on the audio inputs.
"""

ENCODED_WAV = "encoded_wav"
MIDI_MP3 = "midi_mp3"
AUDIO_OUTPUT_FORMATS = (ENCODED_WAV, MIDI_MP3)


def default_audio_output(profile_key):
    """Choose the format whenever a preparation dialog is opened."""
    return MIDI_MP3 if profile_key == "enspire" else ENCODED_WAV


def default_split_audio(profile_key):
    """ENSPIRE USB delivery needs decoded pairs instead of an encoded track."""
    return profile_key == "enspire"


def audio_playback_note_key(profile_key, output_format):
    """Return the Smart PianoSoft message suffix describing the chosen route."""
    if output_format not in AUDIO_OUTPUT_FORMATS:
        raise ValueError(f"Unsupported synchronized audio output: {output_format}")
    if output_format == MIDI_MP3:
        return "output_note_enspire_pair" if profile_key == "enspire" else "output_note_pair_generic"
    if profile_key == "mark_iv":
        return "output_note_mark_iv"
    if profile_key in {"mark_iii", "e3_850", "enspire"}:
        return "output_note_external"
    return "output_note_unverified"
