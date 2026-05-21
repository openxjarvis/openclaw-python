"""Cron delivery preview resolution — mirrors openclaw/src/cron/delivery-preview.ts."""
from __future__ import annotations

from typing import Any

from .normalize import resolve_cron_delivery_plan
from .types import CronJob


def _format_target(channel: str | None, to: str | None) -> str:
    if not channel:
        return "last"
    if to:
        return f"{channel}:{to}"
    return channel


def _format_delivery_detail(
    *,
    requested_channel: str | None,
    resolved: bool,
    session_key: str | None = None,
    error: str | None = None,
) -> str:
    if requested_channel == "last" or not requested_channel:
        if not resolved:
            return (
                f"last -> no route, will fail-closed: {error}"
                if error
                else "last -> no route, will fail-closed"
            )
        return (
            f"resolved from last, session {session_key}"
            if session_key
            else "resolved from last, main session"
        )
    return "explicit" if resolved else (error or "unresolved")


async def resolve_cron_delivery_preview(
    *,
    cfg: Any,
    default_agent_id: str | None,
    job: CronJob,
) -> dict[str, str]:
    """Resolve a single job's delivery preview."""
    plan = resolve_cron_delivery_plan(job)
    if not plan.get("requested") and plan.get("mode") == "none" and not job.delivery:
        return {"label": "not requested", "detail": "not requested"}
    if plan.get("mode") == "webhook":
        target = f"webhook:{plan['to']}" if plan.get("to") else "webhook"
        detail = "webhook" if plan.get("to") else "webhook target missing"
        return {"label": target, "detail": detail}

    from .isolated_agent.delivery import resolve_delivery_target

    requested_channel = plan.get("channel") or "last"
    agent_id = (job.agent_id or "").strip() or default_agent_id or "default"
    target = await resolve_delivery_target(job, cfg=cfg, agent_id=agent_id)
    label_base = f"{plan.get('mode')} -> {_format_target(requested_channel, plan.get('to'))}"

    if target.error is not None:
        err_msg = str(target.error)
        return {
            "label": label_base,
            "detail": _format_delivery_detail(
                requested_channel=requested_channel,
                resolved=False,
                session_key=job.session_key,
                error=err_msg,
            ),
        }

    resolved_label = f"{plan.get('mode')} -> {_format_target(target.channel, target.to)}"
    return {
        "label": resolved_label,
        "detail": _format_delivery_detail(
            requested_channel=requested_channel,
            resolved=True,
            session_key=job.session_key,
        ),
    }


async def resolve_cron_delivery_previews(
    *,
    cfg: Any,
    default_agent_id: str | None,
    jobs: list[CronJob],
) -> dict[str, dict[str, str]]:
    """Resolve delivery previews for a page of jobs."""
    previews: dict[str, dict[str, str]] = {}
    for job in jobs:
        previews[job.id] = await resolve_cron_delivery_preview(
            cfg=cfg,
            default_agent_id=default_agent_id,
            job=job,
        )
    return previews
