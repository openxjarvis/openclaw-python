"""Finalizable draft stream controls — mirrors src/channels/draft-stream-controls.ts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

from openclaw.agents.errors import format_error_message
from openclaw.channels.draft_stream_loop import DraftStreamLoop, create_draft_stream_loop

T = TypeVar("T")


@dataclass
class FinalizableDraftStreamState:
    stopped: bool = False
    final: bool = False


@dataclass(frozen=True)
class StopAndClearMessageIdParams(Generic[T]):
    stop_for_clear: Callable[[], Awaitable[None]]
    read_message_id: Callable[[], T | None]
    clear_message_id: Callable[[], None]


@dataclass(frozen=True)
class ClearFinalizableDraftMessageParams(Generic[T]):
    stop_for_clear: Callable[[], Awaitable[None]]
    read_message_id: Callable[[], T | None]
    clear_message_id: Callable[[], None]
    is_valid_message_id: Callable[[Any], bool]
    delete_message: Callable[[T], Awaitable[None]]
    on_delete_success: Callable[[T], None] | None = None
    warn: Callable[[str], None] | None = None
    warn_prefix: str = ""


@dataclass(frozen=True)
class FinalizableDraftLifecycleParams(Generic[T]):
    throttle_ms: float
    state: FinalizableDraftStreamState
    send_or_edit_stream_message: Callable[[str], Awaitable[bool]]
    read_message_id: Callable[[], T | None]
    clear_message_id: Callable[[], None]
    is_valid_message_id: Callable[[Any], bool]
    delete_message: Callable[[T], Awaitable[None]]
    on_delete_success: Callable[[T], None] | None = None
    warn: Callable[[str], None] | None = None
    warn_prefix: str = ""


@dataclass
class FinalizableDraftStreamControls:
    loop: DraftStreamLoop
    update: Callable[[str], None]
    stop: Callable[[], Awaitable[None]]
    seal: Callable[[], Awaitable[None]]
    discard_pending: Callable[[], Awaitable[None]]
    stop_for_clear: Callable[[], Awaitable[None]]


def create_finalizable_draft_stream_controls(
    *,
    throttle_ms: float,
    is_stopped: Callable[[], bool],
    is_final: Callable[[], bool],
    mark_stopped: Callable[[], None],
    mark_final: Callable[[], None],
    send_or_edit_stream_message: Callable[[str], Awaitable[bool]],
) -> FinalizableDraftStreamControls:
    loop = create_draft_stream_loop(
        throttle_ms=throttle_ms,
        is_stopped=is_stopped,
        send_or_edit_stream_message=send_or_edit_stream_message,
    )

    def update(text: str) -> None:
        if is_stopped() or is_final():
            return
        loop.update(text)

    async def stop() -> None:
        mark_final()
        await loop.flush()

    async def stop_for_clear() -> None:
        mark_stopped()
        loop.stop()
        await loop.wait_for_in_flight()

    async def seal() -> None:
        mark_final()
        loop.stop()
        await loop.wait_for_in_flight()

    return FinalizableDraftStreamControls(
        loop=loop,
        update=update,
        stop=stop,
        seal=seal,
        discard_pending=stop_for_clear,
        stop_for_clear=stop_for_clear,
    )


def create_finalizable_draft_stream_controls_for_state(
    *,
    throttle_ms: float,
    state: FinalizableDraftStreamState,
    send_or_edit_stream_message: Callable[[str], Awaitable[bool]],
) -> FinalizableDraftStreamControls:
    return create_finalizable_draft_stream_controls(
        throttle_ms=throttle_ms,
        is_stopped=lambda: state.stopped,
        is_final=lambda: state.final,
        mark_stopped=lambda: setattr(state, "stopped", True),
        mark_final=lambda: setattr(state, "final", True),
        send_or_edit_stream_message=send_or_edit_stream_message,
    )


async def take_message_id_after_stop(params: StopAndClearMessageIdParams[T]) -> T | None:
    await params.stop_for_clear()
    message_id = params.read_message_id()
    params.clear_message_id()
    return message_id


async def clear_finalizable_draft_message(params: ClearFinalizableDraftMessageParams[T]) -> None:
    message_id = await take_message_id_after_stop(
        StopAndClearMessageIdParams(
            stop_for_clear=params.stop_for_clear,
            read_message_id=params.read_message_id,
            clear_message_id=params.clear_message_id,
        )
    )
    if not params.is_valid_message_id(message_id):
        return
    try:
        await params.delete_message(message_id)
        if params.on_delete_success is not None:
            params.on_delete_success(message_id)
    except Exception as err:
        if params.warn is not None:
            params.warn(f"{params.warn_prefix}: {format_error_message(err)}")


@dataclass
class FinalizableDraftLifecycle(FinalizableDraftStreamControls):
    clear: Callable[[], Awaitable[None]]


def create_finalizable_draft_lifecycle(
    params: FinalizableDraftLifecycleParams[T],
) -> FinalizableDraftLifecycle:
    controls = create_finalizable_draft_stream_controls_for_state(
        throttle_ms=params.throttle_ms,
        state=params.state,
        send_or_edit_stream_message=params.send_or_edit_stream_message,
    )

    async def clear() -> None:
        await clear_finalizable_draft_message(
            ClearFinalizableDraftMessageParams(
                stop_for_clear=controls.stop_for_clear,
                read_message_id=params.read_message_id,
                clear_message_id=params.clear_message_id,
                is_valid_message_id=params.is_valid_message_id,
                delete_message=params.delete_message,
                on_delete_success=params.on_delete_success,
                warn=params.warn,
                warn_prefix=params.warn_prefix,
            )
        )

    return FinalizableDraftLifecycle(
        loop=controls.loop,
        update=controls.update,
        stop=controls.stop,
        seal=controls.seal,
        discard_pending=controls.discard_pending,
        stop_for_clear=controls.stop_for_clear,
        clear=clear,
    )


__all__ = [
    "ClearFinalizableDraftMessageParams",
    "FinalizableDraftLifecycle",
    "FinalizableDraftLifecycleParams",
    "FinalizableDraftStreamControls",
    "FinalizableDraftStreamState",
    "StopAndClearMessageIdParams",
    "clear_finalizable_draft_message",
    "create_finalizable_draft_lifecycle",
    "create_finalizable_draft_stream_controls",
    "create_finalizable_draft_stream_controls_for_state",
    "take_message_id_after_stop",
]
