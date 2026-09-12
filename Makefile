.PHONY: check release-check appimage

PYTHON ?= python3

check:
	$(PYTHON) -m py_compile aps_midi_prep_tool.py aps_midi_prep_tool_app/*.py aps_midi_prep_tool_app/additional_formats/*.py aps_midi_prep_tool_app/helpers/*.py scripts/*.py
	$(PYTHON) -m pytest -q

release-check: check
	git diff --check
	bash -n scripts/build_appimage.sh

appimage:
	./scripts/build_appimage.sh
