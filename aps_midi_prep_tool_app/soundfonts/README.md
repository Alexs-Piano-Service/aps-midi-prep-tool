# Preview SoundFont

Place the bundled File Inspection preview SoundFont here as `default.sf2` or
`default.sf3`.

The app searches this folder before checking environment variables or system
SoundFont locations, so release builds can ship a consistent acoustic piano
preview on Linux, Windows, and macOS.

Use only a SoundFont whose license allows redistribution with the app. FluidR3
GM is MIT-licensed but large; a smaller piano-focused SoundFont is also fine if
it renders standard MIDI channel/program data well.
