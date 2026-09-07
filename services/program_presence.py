from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor

from services.program_gate import ProgramGate


PRESENCE_INTERVAL_MS = 30 * 60 * 1_000


class ProgramPresence:
    """Best-effort monitoring only; failures never change access permissions.

    Calls originate on the GUI thread. One worker preserves start/stop order
    without making the GUI wait for the management server.
    """

    def __init__(self, version: str) -> None:
        self._gate = ProgramGate(version, timeout=2)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="presence")
        self._future: Future | None = None
        self._closed = False

    def heartbeat(self) -> None:
        if self._closed or (self._future is not None and not self._future.done()):
            return
        self._future = self._executor.submit(self._gate.set_connection, True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Queue the final offline report after any in-flight online report.
        self._future = self._executor.submit(self._gate.set_connection, False)
        self._executor.shutdown(wait=False)
