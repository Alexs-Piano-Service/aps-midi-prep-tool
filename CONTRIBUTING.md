# Contributing

Thanks for helping improve APS MIDI Prep Tool.

## Ground Rules

- Test with copies whenever possible. Do not risk the only copy of a floppy,
  disk image, or customer file.
- Do not upload copyrighted disks, commercial MIDI libraries, proprietary
  firmware, private customer files, or other material you do not have the right
  to share.
- Keep bug reports focused on reproducible behavior. If a sample file is needed,
  use a public-domain or self-created file, or coordinate privately first.
- Be careful with physical floppy operations. Formatting and writing can destroy
  data on the selected disk.
- Refer to third-party products, formats, and trademarks only for compatibility
  or preservation context. Do not imply endorsement or affiliation with their
  owners.

## Development Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install PySide6
python3 aps_midi_prep_tool.py
```

Image, floppy, and audio workflows may also need `mtools`, the Greaseweazle CLI,
FluidSynth with a redistributable SoundFont, and LAME.

## Before Submitting Changes

Run the basic syntax and whitespace checks:

```bash
python3 -m py_compile aps_midi_prep_tool.py aps_midi_prep_tool_app/*.py
git diff --check
```

When changing floppy, image, E-SEQ, or MIDI conversion behavior, test with
copies of representative files and note what workflow you verified.

## Documentation

Keep `README.md` user-focused, keep `CHANGELOG.md` updated, and update
`aps_midi_prep_tool_app/eseq_reference.md` when E-SEQ behavior changes.

## License

By contributing, you agree that your contribution is provided under the Apache
License, Version 2.0.
