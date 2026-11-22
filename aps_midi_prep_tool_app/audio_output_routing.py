"""Keep listening previews attached to the system's current audio output."""

import weakref
from dataclasses import dataclass

from PySide6.QtCore import QObject
from PySide6.QtMultimedia import QMediaDevices


@dataclass
class _OutputBinding:
    output: weakref.ReferenceType
    forget: object
    player: weakref.ReferenceType | None = None
    reconnect: bool = False


class DefaultAudioOutputFollower(QObject):
    """Refresh output devices without restarting or seeking media players.

    QAudioOutput resolves the system default when it is constructed. Keeping
    that object alone does not follow later headphone/default-device changes.
    One follower can cover both outputs during an inspection preview crossfade.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._outputs = {}
        self._devices = QMediaDevices(self)
        self._devices.audioOutputsChanged.connect(self.refresh)

    def add_output(self, output, player=None):
        token = id(output)
        if token not in self._outputs:
            owner = weakref.ref(self)

            def forget(*_args):
                follower = owner()
                if follower is not None:
                    follower._outputs.pop(token, None)

            self._outputs[token] = _OutputBinding(weakref.ref(output, forget), forget)
            output.destroyed.connect(forget)
        self._outputs[token].player = weakref.ref(player) if player is not None else None
        self.refresh()

    def remove_output(self, output):
        registered = self._outputs.pop(id(output), None)
        if registered is not None:
            try:
                output.destroyed.disconnect(registered.forget)
            except (RuntimeError, TypeError):
                pass

    def refresh(self):
        device = self._devices.defaultAudioOutput()
        if device.isNull():
            # Qt cannot bind a nonexistent device. A later hotplug signal will
            # provide the new default without changing the user's Play state.
            for binding in self._outputs.values():
                binding.reconnect = True
            return
        for token, binding in list(self._outputs.items()):
            output = binding.output()
            if output is None:
                self._outputs.pop(token, None)
                continue
            try:
                if output.device() != device:
                    output.setDevice(device)
                elif binding.reconnect and binding.player is not None:
                    player = binding.player()
                    if player is not None and player.audioOutput() is output:
                        # The same headphones may return with the same device
                        # descriptor. setDevice would then be a Qt no-op, so
                        # reattach the output to recreate its stale audio sink.
                        # Neither call changes source, position or Play state.
                        player.setAudioOutput(None)
                        player.setAudioOutput(output)
                binding.reconnect = False
            except RuntimeError:
                # A deferred QObject deletion can precede wrapper collection.
                self._outputs.pop(token, None)
