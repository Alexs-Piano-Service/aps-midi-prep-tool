# SoundFonts

This folder can hold an optional development or release SoundFont named
`default.sf2` or `default.sf3`.

The app searches downloaded user SoundFonts first, then this folder, then
environment variables and system SoundFont locations. Release builds do not
bundle FluidSynth by default, so most users should install SoundFonts through
the in-app manager and use a system FluidSynth install for SoundFont-based
preview or rendering.

Use only a SoundFont whose license allows redistribution with the app. FluidR3
GM is MIT-licensed but large; a smaller piano-focused SoundFont is also fine if
it renders standard MIDI channel/program data well.

## Downloadable SoundFont Catalog

The SoundFont manager reads:

```text
https://www.alexanderpeppe.com/aps-midi-prep-tool-data/soundfonts.json
```

The manifest is a JSON object with `schema_version`, `updated`, and a
`soundfonts` array. A plain array is still accepted for testing. Each item can
use this schema:

```json
{
  "id": "musescore-general-sf3",
  "name": "MuseScore General",
  "subtitle": "Recommended default General MIDI SoundFont",
  "category": "General MIDI",
  "format": "sf3",
  "recommended": true,
  "default_for": ["general_midi_rendering", "midi_preview"],
  "download_url": "https://example.com/MuseScore_General.sf3",
  "homepage_url": "https://example.com/",
  "license": "MIT",
  "license_url": "https://example.com/license",
  "approx_size": "38 MB",
  "attribution": "Attribution text.",
  "notes": "Optional user-facing guidance.",
  "sha256": "optional-lowercase-sha256"
}
```

Use `download_url` for the downloadable file. `url` is accepted as an older
alias. URLs may be absolute or relative to the manifest URL. Direct `.sf2` and
`.sf3` files install directly; `.zip`, `.tar`, `.tar.gz`, `.tar.bz2`, and
`.tar.xz` downloads are unpacked and the largest `.sf2`/`.sf3` inside is
installed. `.7z` downloads require a local `7z`/`7za` command, otherwise the app
marks them as manual-install entries. Downloaded files are stored in the user's
application data folder under `soundfonts/`.
