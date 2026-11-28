"""Structured read phases carried by the existing three-argument callback."""


class ReadProgressMessage(str):
    """Remain a normal status string for non-UI progress consumers."""

    def __new__(cls, message, phase):
        if phase not in ("inspect", "read", "organize"):
            raise ValueError(f"Unknown disk read phase: {phase}")
        result = super().__new__(cls, message)
        result.read_phase = phase
        return result
