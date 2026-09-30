"""Developer-only playback checks using a real offline synthesizer."""

from array import array
from dataclasses import dataclass
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import wave

import pytest


def pytest_addoption(parser):
    parser.getgroup("Audio integration tests").addoption(
        "--audio-require-tools", action="store_true",
        default=os.environ.get("APS_REQUIRE_AUDIO_TESTS") == "1",
        help="Fail instead of skipping playback tests when FluidSynth or a SoundFont is missing.",
    )


@dataclass
class Audio:
    samples: array
    sample_rate: int
    channels: int

    @property
    def duration(self):
        return len(self.samples) / self.channels / self.sample_rate

    def _window(self, start, end):
        assert 0 <= start < end <= self.duration, (
            f"Audio ends at {self.duration:.3f}s; expected window {start:.3f}–{end:.3f}s"
        )
        first = round(start * self.sample_rate) * self.channels
        last = round(end * self.sample_rate) * self.channels
        return self.samples[first:last]

    def rms(self, start, end):
        samples = self._window(start, end)
        return math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768

    def assert_pitch(self, start, end, midi_note):
        """Check spectral energy at the expected fundamental, not a WAV hash.

        A Hann window limits leakage. The expected frequency must dominate its
        neighboring semitones, allowing different piano samples and synth versions.
        Tests also check volume separately so numerical noise cannot pass.
        """
        samples = self._window(start, end)
        # Use the loudest channel, avoiding cancellation when mixing stereo samples.
        channel = max(range(self.channels), key=lambda c: sum(x * x for x in samples[c::self.channels]))
        mono = samples[channel::self.channels]
        windowed = [value * (0.5 - 0.5 * math.cos(2 * math.pi * i / (len(mono) - 1)))
                    for i, value in enumerate(mono)]

        def power(note):
            frequency = 440 * 2 ** ((note - 69) / 12)
            coefficient = 2 * math.cos(2 * math.pi * frequency / self.sample_rate)
            previous = before_previous = 0.0
            for value in windowed:
                current = value + coefficient * previous - before_previous
                before_previous, previous = previous, current
            return previous ** 2 + before_previous ** 2 - coefficient * previous * before_previous

        expected = power(midi_note)
        neighbors = max(power(midi_note - 1), power(midi_note + 1))
        assert expected > max(1.0, neighbors * 4), (
            f"Expected audible pitch {midi_note} at {start:.3f}–{end:.3f}s; "
            f"fundamental/neighbor power ratio={expected / max(neighbors, 1):.2f}"
        )


@pytest.fixture(scope="session")
def audio_tools(request):
    configured_command = os.environ.get("APS_TEST_FLUIDSYNTH")
    command = shutil.which(configured_command or "fluidsynth")
    configured_font = os.environ.get("APS_TEST_SOUNDFONT")
    candidates = [Path(configured_font).expanduser()] if configured_font else [
        Path("/usr/share/sounds/sf2/TimGM6mb.sf2"),
        Path("/usr/share/sounds/sf2/FluidR3_GM.sf2"),
        Path("/usr/share/soundfonts/FluidR3_GM.sf2"),
        Path(__file__).resolve().parents[2] / "aps_midi_prep_tool_app/soundfonts/default.sf2",
    ]
    soundfont = next((path for path in candidates if path.is_file()), None)
    if not command or soundfont is None:
        message = (
            "Playback checks require FluidSynth and a GM piano SoundFont. "
            "Set APS_TEST_FLUIDSYNTH and APS_TEST_SOUNDFONT to their paths."
        )
        if request.config.getoption("--audio-require-tools") or configured_command or configured_font:
            pytest.fail(message)
        pytest.skip(message + " Use --audio-require-tools to require this check.")
    return command, soundfont.resolve()


@pytest.fixture
def render_midi(audio_tools):
    command, soundfont = audio_tools

    def render(path):
        path = Path(path)
        output = path.with_suffix(".wav")
        # No audio device, MIDI input, interactive shell, or realtime playback.
        # SDL can probe PulseAudio at library startup even during offline rendering.
        environment = {**os.environ, "SDL_AUDIODRIVER": "dummy"}
        args = [command, "-ni", "-q", "-R", "0", "-C", "0", "-g", "0.5",
                "-r", "22050", "-F", str(output), "-T", "wav", "-O", "s16",
                str(soundfont), str(path)]
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=30, env=environment)
        except subprocess.TimeoutExpired:
            pytest.fail(f"FluidSynth timed out rendering {path.name}")
        assert result.returncode == 0, f"FluidSynth failed: {result.stdout}\n{result.stderr}"
        assert output.is_file(), f"FluidSynth produced no WAV: {result.stderr}"
        with wave.open(str(output), "rb") as wav:
            assert wav.getsampwidth() == 2 and wav.getcomptype() == "NONE"
            samples = array("h", wav.readframes(wav.getnframes()))
            if sys.byteorder != "little":
                samples.byteswap()
            assert samples, "FluidSynth produced an empty WAV"
            return Audio(samples, wav.getframerate(), wav.getnchannels())

    return render
