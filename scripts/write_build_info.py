"""Write the source identity for inclusion in a packaged application."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aps_midi_prep_tool_app.build_info import checkout_identity

if __name__ == "__main__":
    destination = Path(sys.argv[1])
    identity = checkout_identity(ROOT)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(identity) + "\n", encoding="utf-8")
