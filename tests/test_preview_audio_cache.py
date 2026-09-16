import os
import time
import wave
from unittest.mock import Mock

from aps_midi_prep_tool_app import main_window
from aps_midi_prep_tool_app.preview_audio_cache import PreviewAudioCache, file_identity, preview_cache_key


def write_wav(path, value=1):
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(bytes([value, 0]) * 100)


def test_completed_render_reused_after_switching_song_and_settings(tmp_path, monkeypatch):
    cache = PreviewAudioCache(tmp_path / "cache")
    monkeypatch.setattr(main_window, "_preview_audio_cache", lambda: cache)
    monkeypatch.setattr(main_window, "_find_preview_soundfont", lambda: "")
    monkeypatch.setattr(main_window, "_find_fluidsynth_command", lambda: "")
    render = Mock(side_effect=lambda notes, path, duration, **kwargs: write_wav(path, notes[0]["velocity"]))
    monkeypatch.setattr(main_window, "_write_preview_wav", render)
    output = tmp_path / "preview.wav"
    results = []
    for velocity in (10, 10, 20, 10):
        worker = main_window.MidiPreviewRenderWorker(
            b"midi", [{"velocity": velocity}], 1.0, str(output),
        )
        ready, errors = [], []
        worker.previewReady.connect(lambda path, engine: ready.append((path, engine)))
        worker.previewFailed.connect(errors.append)
        worker.run()
        assert not errors
        assert ready == [(str(output), "Built-in piano preview")]
        results.append(output.read_bytes())
    assert render.call_count == 2
    assert results[0] == results[1] == results[3]
    assert results[2] != results[0]


def test_key_changes_for_audio_inputs_and_updated_soundfont(tmp_path):
    font = tmp_path / "piano.sf3"
    font.write_bytes(b"font")
    identity = [file_identity(font), None]
    original = preview_cache_key(b"midi", [{"velocity": 90}], 1.0, identity)
    assert original == preview_cache_key(b"midi", [{"velocity": 90}], 1.0, identity)
    assert original != preview_cache_key(b"edited midi", [{"velocity": 90}], 1.0, identity)
    assert original != preview_cache_key(b"midi", [{"velocity": 60}], 1.0, identity)
    assert original != preview_cache_key(b"midi", [{"velocity": 90}], 2.0, identity)
    font.write_bytes(b"new font")
    assert original != preview_cache_key(b"midi", [{"velocity": 90}], 1.0, [file_identity(font), None])
    assert original != preview_cache_key(b"midi", [{"velocity": 90}], 1.0, [identity[0], ["fluidsynth"]])


def test_incomplete_and_cancelled_renders_are_not_cached(tmp_path, monkeypatch):
    cache = PreviewAudioCache(tmp_path / "cache")
    monkeypatch.setattr(main_window, "_preview_audio_cache", lambda: cache)
    monkeypatch.setattr(main_window, "_find_preview_soundfont", lambda: "")
    monkeypatch.setattr(main_window, "_find_fluidsynth_command", lambda: "")
    monkeypatch.setattr(main_window, "_write_preview_wav", Mock(side_effect=RuntimeError("Preview generation cancelled.")))
    output = tmp_path / "preview.wav"
    worker = main_window.MidiPreviewRenderWorker(b"midi", [], 1.0, str(output))
    errors = []
    worker.previewFailed.connect(errors.append)
    worker.run()
    assert errors == ["Preview generation cancelled."]
    assert not output.exists()
    assert not list((tmp_path / "cache").glob("*.json"))


def test_temporary_fluidsynth_failure_does_not_cache_fallback_as_soundfont(tmp_path, monkeypatch):
    cache = PreviewAudioCache(tmp_path / "cache")
    monkeypatch.setattr(main_window, "_preview_audio_cache", lambda: cache)
    monkeypatch.setattr(main_window, "_find_preview_soundfont", lambda: "piano.sf3")
    monkeypatch.setattr(main_window, "_find_fluidsynth_command", lambda: "fluidsynth")
    fallback = Mock(side_effect=lambda notes, path, duration, **kwargs: write_wav(path, 10))
    monkeypatch.setattr(main_window, "_write_preview_wav", fallback)
    attempts = []

    def synth_render(worker, midi_path, font):
        attempts.append(font)
        if len(attempts) == 1:
            raise RuntimeError("Temporary synth failure")
        write_wav(worker.output_path, 20)
        return True, "FluidSynth + Piano"

    monkeypatch.setattr(main_window.MidiPreviewRenderWorker, "_render_with_fluidsynth", synth_render)
    labels = []
    for index in range(3):
        worker = main_window.MidiPreviewRenderWorker(b"midi", [], 1.0, str(tmp_path / f"{index}.wav"))
        worker.previewReady.connect(lambda path, engine: labels.append(engine))
        worker.run()
    assert labels == ["Built-in piano preview", "FluidSynth + Piano", "FluidSynth + Piano"]
    assert attempts == ["piano.sf3", "piano.sf3"]
    assert fallback.call_count == 1


def test_corrupt_or_evicted_cache_regenerates_without_deleting_playing_file(tmp_path):
    cache = PreviewAudioCache(tmp_path / "cache", max_entries=1)
    source = tmp_path / "source.wav"
    write_wav(source)
    cache.store("first", source, "piano")
    playing = tmp_path / "playing.wav"
    assert cache.restore("first", playing) == "piano"
    original = playing.read_bytes()
    cache.store("second", source, "piano")
    assert cache.restore("first", tmp_path / "missing.wav") is None
    assert playing.read_bytes() == original
    (cache.directory / "second.wav").write_bytes(b"broken")
    assert cache.restore("second", tmp_path / "broken.wav") is None


def test_cache_prunes_bytes_and_stale_orphans(tmp_path):
    cache = PreviewAudioCache(tmp_path / "cache", max_bytes=300)
    source = tmp_path / "source.wav"
    write_wav(source)
    cache.store("first", source, "piano")
    orphan = cache.directory / "orphan.wav"
    orphan.write_bytes(b"orphan")
    old = time.time() - 7200
    os.utime(orphan, (old, old))
    cache.store("second", source, "piano")
    assert not orphan.exists()
    assert sum(path.stat().st_size for path in cache.directory.glob("*.wav")) <= 300


def test_repeated_play_uses_loaded_file_without_renderer(tmp_path, monkeypatch):
    output = tmp_path / "preview.wav"
    write_wav(output)
    identity = [None, None]
    monkeypatch.setattr(main_window, "_preview_renderer_identity", lambda path: identity)
    dialog = Mock()
    dialog.visible_notes = [object()]
    dialog.preview_render_worker = None
    dialog.preview_audio_path = str(output)
    dialog._preview_audio_stale = False
    dialog._preview_renderer_identity = identity
    dialog._using_midi_output.return_value = False
    main_window.FileInspectionDialog._play_current_file(dialog)
    main_window.FileInspectionDialog._play_current_file(dialog)
    assert dialog._start_preview_playback.call_count == 2
    dialog._start_audio_preview_render.assert_not_called()
    dialog._start_live_fluidsynth_playback.assert_not_called()


def test_replaced_soundfont_invalidates_loaded_preview(tmp_path, monkeypatch):
    output = tmp_path / "preview.wav"
    write_wav(output)
    monkeypatch.setattr(main_window, "_preview_renderer_identity", lambda path: ["updated", None])
    dialog = Mock()
    dialog.visible_notes = [object()]
    dialog.preview_render_worker = None
    dialog.preview_audio_path = str(output)
    dialog._preview_audio_stale = False
    dialog._preview_renderer_identity = ["original", None]
    dialog._using_midi_output.return_value = False
    dialog._clear_preview_audio.side_effect = lambda: setattr(dialog, "preview_audio_path", "")
    main_window.FileInspectionDialog._play_current_file(dialog)
    dialog._clear_preview_audio.assert_called_once()
    dialog._start_preview_playback.assert_not_called()
    dialog._start_audio_preview_render.assert_called_once_with(autoplay=True)
