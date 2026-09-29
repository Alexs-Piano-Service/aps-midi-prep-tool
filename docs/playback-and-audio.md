# Listen to songs and create audio copies

[All user guides](README.md) · [Getting started](getting-started.md)

## Preview a song

1. Load songs using **Open MIDI Folder** or **Open Image**.
2. Open **Utilities → File Inspection...** and select a song in its list.
3. Set **Playback output** to **Audio preview** for your computer's speakers,
   or select a connected MIDI device. Use **Refresh** after connecting a device.
4. Choose **Play**. You can mute channels or adjust the preview mix and tempo
   without changing the saved song.

The piano roll shows notes, channels, instruments, tempo, and pedals. Basic
piano preview works without a SoundFont. A SoundFont supplies instrument sounds
for richer playback; use **Download SoundFonts...** to add one. SoundFont
playback requires FluidSynth. See [optional tools](../README.md#compatibility-and-optional-tools).

The first audio preview renders the song; subsequent Play clicks reuse that
audio. Completed previews are cached across inspection sessions, with automatic
replacement when the song, preview mix, SoundFont, or renderer changes.

## Create WAV or MP3 copies

1. Load the songs you want to render, then open **Utilities → Render Audio...**.
   This utility renders all currently listed MIDI or E-SEQ songs.
2. Select a SoundFont, or choose **Download SoundFonts...** to add one.
3. Choose **WAV** or **MP3** and an output folder.
4. Choose **Render** and check the completion result.

SoundFont rendering requires FluidSynth and a SoundFont; MP3 also requires
LAME. The dialog explains missing requirements. Basic piano preview does not
require FluidSynth, but it is not the SoundFont renderer used by this utility.
See [optional tools](../README.md#compatibility-and-optional-tools).

## Inspect E-SEQ details

For E-SEQ songs, **File details** includes the original header's startup tempo,
tempo factors, meter, pedal and channel flags, write protection, and display
mode. Raw values accompany their meanings, and uncertain fields remain marked
as uninterpreted. The decoded MIDI preview appears below the source details.

## Convert tracks or merge piano parts

File Inspection also has **Convert to Type 0** and **Merge Channels to Piano**
buttons. Type 0 conversion accepts MIDI; piano merging accepts MIDI and E-SEQ.
Each stages an edit and refreshes the preview. Type 0 combines tracks
while keeping channels and instruments; merging routes note parts to MIDI
channel 1 and selects Acoustic Grand Piano while
keeping the source format. E-SEQ edits preserve native timing, titles, song order,
and Yamaha's separate pedal-detail data. These edits use the complete song,
including hidden or muted preview channels. Use **Save** to write them or
**Edit → Undo** to revert.

### Choose how overlapping notes are handled

When a piano merge finds overlapping notes on the same key, it asks how to
handle them. **Smart repair** removes long notes covering multiple shorter
strikes, keeps the shortest simultaneous note, and trims remaining overlaps.
**Keep attacks** keeps the longest simultaneous note and trims overlaps at the
next attack. Both combine duplicate unisons and use exact tick boundaries,
without moving attacks or resuming discarded tails. **Merge only** keeps the
existing overlapping-note behavior. Chords on different keys, notes routed to
different MIDI ports, and notes that only touch do not prompt. Repairs preserve
each note's MIDI destination, including generated releases. The same choice
applies when combining instruments as piano during Type 0 conversion.

Check **Use this behavior for all future channel merges** to remember the choice
across files and app restarts. Without that check, each affected song asks again.
Open **Settings → Overlapping Piano Notes...** any time to change the behavior,
or uncheck the box and save to restore the prompt for each affected song.
**Settings → Show Dismissed Messages Again** also restores the prompt for future overlaps.

**Edit → Merge Channels to Piano...** applies the same merge to one song or
all listed MIDI and E-SEQ songs, including files being edited inside a disk image.

### Advanced: immediate-mute messages when merging

Channel merging and Type 0 piano remapping translate All Notes Off (CC123) and
the related mode commands (CC124–127) into note releases for their source parts.
All Sound Off (CC120) instead requires an immediate mute, including sustained
notes and release tails. APS retains CC120 when no other source part could still
be sounding. Otherwise, it uses note releases as a lossy approximation and flags
the removed CC120 in Conversion details; sustain or release tails may continue.
Because MIDI does not specify a receiver's release duration, a part remains
potentially sounding after its note-offs until a retained immediate mute or
recognized system reset. Keep the original channels (including ordinary Type 0
conversion without piano remapping) when exact source-specific CC120 behavior
is required. See the [MIDI message definitions](https://midi.org/summary-of-midi-1-0-messages).

[![MIDI notes, channels, tempo, and pedals in File Inspection](images/aps-midi-prep-tool-midi-file-inspection-piano-roll.png)](images/aps-midi-prep-tool-midi-file-inspection-piano-roll.png)

[All user guides](README.md) · [Review conversion reports](preparation-reliability.md#understand-conversion-reports)
