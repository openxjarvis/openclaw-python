"""Coroutine-driven wizard session — mirrors TS src/wizard/session.ts

The WizardSession runs the actual wizard logic (runner) as a concurrent
coroutine.  When the runner needs user input it calls the prompter which
suspends the runner until the RPC client answers via ``answer()``.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional

logger = logging.getLogger(__name__)

WizardSessionStatus = Literal["running", "done", "cancelled", "error"]


@dataclass
class WizardStepOption:
    value: Any
    label: str
    hint: str | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"value": self.value, "label": self.label}
        if self.hint:
            d["hint"] = self.hint
        return d


@dataclass
class WizardStep:
    id: str
    type: str  # "note" | "select" | "text" | "confirm" | "multiselect" | "progress" | "action"
    title: str | None = None
    message: str | None = None
    options: list[WizardStepOption] | None = None
    initial_value: Any = None
    placeholder: str | None = None
    sensitive: bool = False
    executor: str | None = None  # "gateway" | "client"

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "type": self.type}
        if self.title:
            d["title"] = self.title
        if self.message is not None:
            d["message"] = self.message
        if self.options:
            d["options"] = [o.to_dict() for o in self.options]
        if self.initial_value is not None:
            d["initialValue"] = self.initial_value
        if self.placeholder:
            d["placeholder"] = self.placeholder
        if self.sensitive:
            d["sensitive"] = True
        if self.executor:
            d["executor"] = self.executor
        return d


@dataclass
class WizardNextResult:
    done: bool
    step: WizardStep | None = None
    status: WizardSessionStatus = "running"
    error: str | None = None

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"done": self.done, "status": self.status}
        if self.step:
            d["step"] = self.step.to_dict()
        if self.error:
            d["error"] = self.error
        return d


class WizardCancelledError(Exception):
    """Raised inside the runner when the session is cancelled."""


class WizardPrompter:
    """Adapts wizard prompts to deferred step objects.

    Each prompt method creates a :class:`WizardStep`, pushes it to the
    session, and awaits the answer future that the RPC layer will resolve.
    """

    def __init__(self, session: WizardSession) -> None:
        self._session = session

    async def intro(self, title: str) -> None:
        await self._prompt(WizardStep(id="", type="note", title=title, message="", executor="client"))

    async def outro(self, message: str) -> None:
        await self._prompt(WizardStep(id="", type="note", title="Done", message=message, executor="client"))

    async def note(self, message: str, title: str | None = None) -> None:
        await self._prompt(WizardStep(id="", type="note", title=title, message=message, executor="client"))

    async def select(self, params: dict) -> Any:
        options = [
            WizardStepOption(value=o.get("value"), label=o.get("label", ""), hint=o.get("hint"))
            for o in params.get("options", [])
        ]
        return await self._prompt(WizardStep(
            id="", type="select",
            message=params.get("message"),
            options=options,
            initial_value=params.get("initialValue"),
            executor="client",
        ))

    async def multiselect(self, params: dict) -> list:
        options = [
            WizardStepOption(value=o.get("value"), label=o.get("label", ""), hint=o.get("hint"))
            for o in params.get("options", [])
        ]
        res = await self._prompt(WizardStep(
            id="", type="multiselect",
            message=params.get("message"),
            options=options,
            initial_value=params.get("initialValues"),
            executor="client",
        ))
        return list(res) if isinstance(res, (list, tuple)) else []

    async def text(self, params: dict) -> str:
        res = await self._prompt(WizardStep(
            id="", type="text",
            message=params.get("message"),
            initial_value=params.get("initialValue"),
            placeholder=params.get("placeholder"),
            executor="client",
        ))
        return str(res) if res is not None else ""

    async def confirm(self, params: dict) -> bool:
        res = await self._prompt(WizardStep(
            id="", type="confirm",
            message=params.get("message"),
            initial_value=params.get("initialValue"),
            executor="client",
        ))
        return bool(res)

    async def password(self, params: dict) -> str:
        res = await self._prompt(WizardStep(
            id="", type="text",
            message=params.get("message"),
            sensitive=True,
            executor="client",
        ))
        return str(res) if res is not None else ""

    def progress(self, label: str) -> dict:
        return {"update": lambda _msg: None, "stop": lambda _msg: None}

    async def _prompt(self, step: WizardStep) -> Any:
        step.id = str(uuid.uuid4())
        return await self._session.await_answer(step)


class WizardSession:
    """Coroutine-driven wizard session — mirrors TS WizardSession.

    The ``runner`` coroutine is started immediately.  Each time it needs
    user input the prompter suspends it via an asyncio Future.  The RPC
    layer calls ``next()`` to get the pending step and ``answer()`` to
    resume the runner.
    """

    def __init__(
        self,
        runner: Callable[[WizardPrompter], Awaitable[None]],
    ) -> None:
        self._status: WizardSessionStatus = "running"
        self._error: str | None = None
        self._current_step: WizardStep | None = None
        self._step_event: asyncio.Event = asyncio.Event()
        self._answer_futures: dict[str, asyncio.Future[Any]] = {}
        self._runner_task: asyncio.Task | None = None

        prompter = WizardPrompter(self)
        self._runner_task = asyncio.ensure_future(self._run(runner, prompter))

    async def _run(
        self,
        runner: Callable[[WizardPrompter], Awaitable[None]],
        prompter: WizardPrompter,
    ) -> None:
        try:
            await runner(prompter)
            self._status = "done"
        except WizardCancelledError:
            self._status = "cancelled"
            self._error = "cancelled"
        except Exception as exc:
            self._status = "error"
            self._error = str(exc)
            logger.exception("Wizard runner failed")
        finally:
            self._current_step = None
            self._step_event.set()

    async def next(self) -> WizardNextResult:
        if self._current_step:
            return WizardNextResult(done=False, step=self._current_step, status=self._status)
        if self._status != "running":
            return WizardNextResult(done=True, status=self._status, error=self._error)
        self._step_event.clear()
        await self._step_event.wait()
        if self._current_step:
            return WizardNextResult(done=False, step=self._current_step, status=self._status)
        return WizardNextResult(done=True, status=self._status, error=self._error)

    async def answer(self, step_id: str, value: Any) -> None:
        fut = self._answer_futures.pop(step_id, None)
        if fut is None:
            raise ValueError("wizard: no pending step")
        self._current_step = None
        fut.set_result(value)

    def cancel(self) -> None:
        if self._status != "running":
            return
        self._status = "cancelled"
        self._error = "cancelled"
        self._current_step = None
        for fut in self._answer_futures.values():
            if not fut.done():
                fut.set_exception(WizardCancelledError())
        self._answer_futures.clear()
        self._step_event.set()

    def push_step(self, step: WizardStep) -> None:
        self._current_step = step
        self._step_event.set()

    async def await_answer(self, step: WizardStep) -> Any:
        if self._status != "running":
            raise WizardCancelledError("wizard: session not running")
        self.push_step(step)
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._answer_futures[step.id] = fut
        return await fut

    def get_status(self) -> WizardSessionStatus:
        return self._status

    def get_error(self) -> str | None:
        return self._error

    def to_dict(self) -> dict:
        step = self._current_step
        return {
            "status": self._status,
            "step": step.to_dict() if step else None,
            "error": self._error,
        }


__all__ = [
    "WizardStep",
    "WizardStepOption",
    "WizardNextResult",
    "WizardSession",
    "WizardSessionStatus",
    "WizardPrompter",
    "WizardCancelledError",
]
