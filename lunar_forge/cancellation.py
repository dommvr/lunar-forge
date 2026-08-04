"""Thread-safe public cancellation control for one agent run."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Event, Lock
from typing import Callable, Iterator, Protocol, runtime_checkable

from lunar_forge.runtime.base import (
    RuntimeRollbackResult,
    RuntimeRollbackStatus,
)


CancelCallback = Callable[[], bool]


@runtime_checkable
class CancellableModelClient(Protocol):
    """Optional model capability used while a completion is active."""

    def cancel_active(self) -> bool:
        """Request best-effort cancellation of the active model operation."""
        ...


@dataclass(frozen=True, slots=True)
class CancellationResult:
    """Public final outcome of an accepted cancellation request."""

    cancelled: bool
    rollback_requested: bool
    model_operation_cancelled: bool
    runtime_command_cancelled: bool
    rollback: RuntimeRollbackResult = field(
        default_factory=lambda: RuntimeRollbackResult(
            RuntimeRollbackStatus.NOT_REQUESTED
        )
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "cancelled": self.cancelled,
            "rollback_requested": self.rollback_requested,
            "model_operation_cancelled": self.model_operation_cancelled,
            "runtime_command_cancelled": self.runtime_command_cancelled,
            "rollback": self.rollback.to_dict(),
        }


class AgentRunCancelled(RuntimeError):
    """Internal control-flow signal raised at a safe agent boundary."""


class CancellationToken:
    """Cancellation request shared safely across tasks or threads.

    ``request_cancel`` is idempotent. The first call returns ``True``; repeated
    calls return ``False`` and do not invoke operation cancellers again. After
    the event iterator finishes its cancellation events, ``wait_result``
    returns the public cancellation and rollback result.
    """

    def __init__(self) -> None:
        self._requested = Event()
        self._result_ready = Event()
        self._lock = Lock()
        self._rollback_requested = False
        self._callbacks: dict[int, tuple[str, CancelCallback]] = {}
        self._next_callback_id = 0
        self._model_cancelled = False
        self._runtime_cancelled = False
        self._result: CancellationResult | None = None

    @property
    def is_cancellation_requested(self) -> bool:
        return self._requested.is_set()

    @property
    def rollback_requested(self) -> bool:
        with self._lock:
            return self._rollback_requested

    @property
    def result(self) -> CancellationResult | None:
        with self._lock:
            return self._result

    def request_cancel(self, *, rollback: bool = True) -> bool:
        """Request cancellation and optionally current-turn rollback."""

        with self._lock:
            if self._requested.is_set():
                if rollback and self._result is None:
                    self._rollback_requested = True
                return False
            self._rollback_requested = rollback
            self._requested.set()
            callbacks = tuple(self._callbacks.values())

        for kind, callback in callbacks:
            cancelled = False
            try:
                cancelled = callback() is True
            except Exception:
                cancelled = False
            if not cancelled:
                continue
            with self._lock:
                if kind == "model":
                    self._model_cancelled = True
                elif kind == "runtime":
                    self._runtime_cancelled = True
        return True

    def wait_result(self, timeout: float | None = None) -> CancellationResult | None:
        """Wait for the run to publish its cancellation/rollback result."""

        if not self._result_ready.wait(timeout):
            return None
        return self.result

    def raise_if_cancelled(self) -> None:
        if self._requested.is_set():
            raise AgentRunCancelled("Agent run cancellation was requested.")

    @contextmanager
    def _bind_canceller(
        self,
        kind: str,
        callback: CancelCallback | None,
    ) -> Iterator[None]:
        callback_id: int | None = None
        if callback is not None:
            with self._lock:
                self._next_callback_id += 1
                callback_id = self._next_callback_id
                self._callbacks[callback_id] = (kind, callback)
                already_requested = self._requested.is_set()
            if already_requested:
                try:
                    cancelled = callback() is True
                except Exception:
                    cancelled = False
                if cancelled:
                    with self._lock:
                        if kind == "model":
                            self._model_cancelled = True
                        elif kind == "runtime":
                            self._runtime_cancelled = True
        try:
            self.raise_if_cancelled()
            yield
            self.raise_if_cancelled()
        finally:
            if callback_id is not None:
                with self._lock:
                    self._callbacks.pop(callback_id, None)

    def _finish(self, rollback: RuntimeRollbackResult) -> CancellationResult:
        with self._lock:
            if self._result is None:
                self._result = CancellationResult(
                    cancelled=True,
                    rollback_requested=self._rollback_requested,
                    model_operation_cancelled=self._model_cancelled,
                    runtime_command_cancelled=self._runtime_cancelled,
                    rollback=rollback,
                )
            result = self._result
            self._result_ready.set()
            return result


__all__ = [
    "CancellationResult",
    "CancellationToken",
    "CancellableModelClient",
]
