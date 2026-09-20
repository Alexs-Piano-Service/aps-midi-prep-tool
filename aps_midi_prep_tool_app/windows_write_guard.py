"""Bound a raw-writer helper's lifetime, including blocked native calls.

Runs only in the disposable writer process. The UI waits for that process to
exit before releasing its write lock or the input image.
"""

import os
import threading
import time

WRITE_TIMEOUT_SECONDS = 300
CANCEL_GRACE_SECONDS = 3


class _WatchdogStop(threading.Event):
    def __init__(self):
        super().__init__()
        self._termination_lock = threading.Lock()

    def set(self):
        # Once completion returns, no already-decided termination may run.
        with self._termination_lock:
            super().set()

    def terminate_unless_stopped(self, force_exit):
        with self._termination_lock:
            if not self.is_set():
                force_exit()


def start_watchdog(cancel_path, request_stop, force_exit, *, timeout=WRITE_TIMEOUT_SECONDS):
    stopped = _WatchdogStop()
    deadline = time.monotonic() + timeout

    def watch():
        requested_at = None
        while not stopped.wait(0.1):
            now = time.monotonic()
            reason = "cancelled" if cancel_path and os.path.exists(cancel_path) else "timeout" if now >= deadline else ""
            if reason and requested_at is None:
                requested_at = now
                # A stop request also persists diagnostics. Keep that work off
                # the watchdog: blocked disk I/O must not disable termination.
                def request(stop_reason=reason):
                    try:
                        request_stop(stop_reason)
                    except Exception:
                        # Forced termination remains available even when the
                        # helper cannot record or acknowledge the request.
                        pass

                threading.Thread(
                    target=request, name="aps-raw-write-stop", daemon=True,
                ).start()
            if requested_at is not None and now - requested_at >= CANCEL_GRACE_SECONDS:
                stopped.terminate_unless_stopped(force_exit)
                return

    thread = threading.Thread(target=watch, name="aps-raw-write-watchdog", daemon=True)
    thread.start()
    return stopped
