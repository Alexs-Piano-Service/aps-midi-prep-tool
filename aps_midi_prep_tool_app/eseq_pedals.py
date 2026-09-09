"""Yamaha binary and detailed pedal layers for fresh Disklavier preparation.

The edge thresholds, zero initialization and repeated-detail suppression follow
the Mark IV converter's hpdlCheck. Selection remains conservative: existing
native layers, binary-only lanes and a channel 3 used for notes are preserved.
"""


# Yamaha's hpdlCheck handles damper and soft pedals. Sostenuto (CC66)
# is not part of its continuous-pedal conversion.
PIANO_PEDAL_CONTROLLERS = frozenset((64, 67))

# (release threshold, press threshold). Release is strictly below its
# threshold; press includes its threshold. CC67's press threshold is 64.
_YAMAHA_PEDAL_THRESHOLDS = {64: (82, 84), 67: (61, 64)}


def yamaha_pedal_controllers(messages):
    """Find channel-1 continuous lanes with an unused channel-3 destination.

    One intermediate value identifies the entire controller lane as continuous;
    its zero and 127 endpoints must travel with it. Binary-only lanes and other
    MIDI channels retain their routing. Existing channel-3 lanes take precedence
    so that preparing an already arranged performance cannot merge two pedals.
    Note events reserve channel 3 for music, matching Yamaha's track-status
    check; controller/program setup alone does not reserve that channel.
    """
    continuous = set()
    occupied = set()
    for raw in messages:
        if len(raw) == 3 and raw[0] in (0x82, 0x92):
            return frozenset()
        if len(raw) != 3 or raw[1] not in PIANO_PEDAL_CONTROLLERS:
            continue
        if raw[0] == 0xB0 and 0 < raw[2] < 127:
            continuous.add(raw[1])
        elif raw[0] == 0xB2:
            occupied.add(raw[1])
    return frozenset(continuous - occupied)


class YamahaPedalConverter:
    """Expand selected source lanes after their original event is scheduled.

    Each call returns zero, one or two wire messages. All returned messages
    belong at that source event's already assigned tick; a synthesized binary
    companion must not become another scheduler input. Even a suppressed
    source event must retain its place in an existing timing policy.

    Yamaha compares against the preceding *raw* value, not the last binary
    output. For example, damper values 84, 83, 84 emit two presses with no
    release between them. Both raw and detailed histories start at zero, so
    an initial zero produces no event. Existing lanes outside ``controllers``
    pass through unchanged, including any redundant binary/native messages.
    """

    def __init__(self, controllers):
        self.controllers = frozenset(controllers)
        if not self.controllers <= PIANO_PEDAL_CONTROLLERS:
            raise ValueError("Yamaha pedal conversion supports only CC64 and CC67.")
        self._previous_raw = dict.fromkeys(self.controllers, 0)
        self._previous_detail = dict.fromkeys(self.controllers, 0)
        self.has_half_pedal = False

    def convert_message(self, raw):
        """Return binary first, then channel-3 detail, without changing time."""
        if len(raw) != 3 or raw[0] != 0xB0 or raw[1] not in self.controllers:
            return (raw,)

        controller, value = raw[1:]
        low, high = _YAMAHA_PEDAL_THRESHOLDS[controller]
        previous = self._previous_raw[controller]
        result = []
        if value >= high and previous < high:
            result.append(bytes((0xB0, controller, 127)))
        elif value < low and previous >= low:
            result.append(bytes((0xB0, controller, 0)))
        self._previous_raw[controller] = value

        if value != self._previous_detail[controller]:
            result.append(bytes((0xB2, controller, value)))
            self._previous_detail[controller] = value
            self.has_half_pedal = True
        return tuple(result)
