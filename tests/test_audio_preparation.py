"""Destination defaults distinguish USB pairs from encoded-audio inputs."""

import pytest

from aps_midi_prep_tool_app.audio_preparation import (
    ENCODED_WAV, MIDI_MP3, audio_playback_note_key,
    default_audio_output, default_split_audio,
)
from aps_midi_prep_tool_app.preparation_profiles import PIANO_PROFILES


# Every selectable target needs an explicit playback route. Generic and other
# manufacturers' profiles must not acquire an unsupported Disklavier claim.
ENCODED_ROUTES = {
    "unsure": "output_note_unverified",
    "custom": "output_note_unverified",
    "mark_i": "output_note_unverified",
    "mark_ii": "output_note_unverified",
    "dsr1": "output_note_unverified",
    "mark_ii_xg": "output_note_unverified",
    "mark_iii": "output_note_external",
    "mark_iv": "output_note_mark_iv",
    "e3_850": "output_note_external",
    "enspire": "output_note_external",
    "midi_export": "output_note_unverified",
    "pianodisc_128plus": "output_note_unverified",
    "pianodisc_228cfx": "output_note_unverified",
    "pianodisc_prodigy": "output_note_unverified",
    "qrs_chili": "output_note_unverified",
    "qrs_pmii": "output_note_unverified",
    "qrs_pno3": "output_note_unverified",
    "qrs_pno4": "output_note_unverified",
}


def test_all_selectable_targets_have_a_reviewed_audio_route():
    assert set(ENCODED_ROUTES) == {profile.key for profile in PIANO_PROFILES}


@pytest.mark.parametrize("profile", ENCODED_ROUTES)
def test_target_defaults_and_manual_playback_routes(profile):
    if profile == "enspire":
        assert default_audio_output(profile) == MIDI_MP3
        assert default_split_audio(profile) is True
        assert audio_playback_note_key(profile, MIDI_MP3) == "output_note_enspire_pair"
    else:
        assert default_audio_output(profile) == ENCODED_WAV
        assert default_split_audio(profile) is False
        assert audio_playback_note_key(profile, MIDI_MP3) == "output_note_pair_generic"
    assert audio_playback_note_key(profile, ENCODED_WAV) == ENCODED_ROUTES[profile]


@pytest.mark.parametrize("profile", (None, "", "unknown-controller", "ENSPIRE"))
def test_missing_or_unrecognized_target_keeps_a_conservative_audio_default(profile):
    assert default_audio_output(profile) == ENCODED_WAV
    assert default_split_audio(profile) is False
    assert audio_playback_note_key(profile, ENCODED_WAV) == "output_note_unverified"
    assert audio_playback_note_key(profile, MIDI_MP3) == "output_note_pair_generic"


@pytest.mark.parametrize("output_format", (None, "", "wav", "mp3", "midi_wav"))
def test_invalid_output_mode_is_rejected(output_format):
    with pytest.raises(ValueError, match="Unsupported synchronized audio output"):
        audio_playback_note_key("enspire", output_format)
