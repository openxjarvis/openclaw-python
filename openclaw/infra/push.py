"""Push notification infrastructure.

Mirrors openclaw/src/infra/push-apns.ts and push-web.ts.

Implements APNs and Web Push (VAPID) notification dispatch.
Python implementations delegate to optional system dependencies:
  - pywebpush  (web push via VAPID)
  - httpx      (APNs HTTP/2)

All functions are safe to call even when the dependencies are absent
or unconfigured — they return graceful error payloads.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _get_vapid_private_key() -> str | None:
    return os.environ.get("OPENCLAW_VAPID_PRIVATE_KEY") or os.environ.get("VAPID_PRIVATE_KEY")


def get_vapid_public_key() -> str | None:
    """Return the VAPID public key from environment / config."""
    return os.environ.get("OPENCLAW_VAPID_PUBLIC_KEY") or os.environ.get("VAPID_PUBLIC_KEY")


def _get_web_push_subscriptions_path() -> Path:
    state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR") or Path.home() / ".openclaw")
    return state_dir / "push_subscriptions.json"


def _load_subscriptions() -> list[dict[str, Any]]:
    path = _get_web_push_subscriptions_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except Exception:
        return []


def _save_subscriptions(subs: list[dict[str, Any]]) -> None:
    path = _get_web_push_subscriptions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(subs, indent=2))


# ---------------------------------------------------------------------------
# Web Push (VAPID)
# ---------------------------------------------------------------------------

async def register_web_push_subscription(
    endpoint: str,
    keys: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> None:
    """Save a web push subscription entry."""
    subs = _load_subscriptions()
    for sub in subs:
        if sub.get("endpoint") == endpoint:
            sub["keys"] = keys
            _save_subscriptions(subs)
            return
    subs.append({"endpoint": endpoint, "keys": keys})
    _save_subscriptions(subs)


async def unregister_web_push_subscription(endpoint: str) -> None:
    """Remove a web push subscription by endpoint."""
    subs = _load_subscriptions()
    subs = [s for s in subs if s.get("endpoint") != endpoint]
    _save_subscriptions(subs)


async def send_web_push_broadcast(
    title: str = "OpenClaw",
    body: str = "Notification",
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Broadcast a web push message to all subscriptions."""
    subs = _load_subscriptions()
    if not subs:
        return {"sent": 0, "errors": 0}

    private_key = _get_vapid_private_key()
    if not private_key:
        return {"sent": 0, "errors": 0, "reason": "VAPID not configured"}

    try:
        from pywebpush import webpush, WebPushException  # type: ignore[import-untyped]
    except ImportError:
        logger.warning("pywebpush not installed — web push unavailable")
        return {"sent": 0, "errors": 0, "reason": "pywebpush not installed"}

    sent = 0
    errors = 0
    payload = json.dumps({"title": title, "body": body, **(data or {})})
    for sub in subs:
        try:
            webpush(
                subscription_info={"endpoint": sub["endpoint"], "keys": sub.get("keys", {})},
                data=payload,
                vapid_private_key=private_key,
                vapid_claims={"sub": "mailto:admin@openclaw.ai"},
            )
            sent += 1
        except Exception as exc:
            logger.warning(f"Web push send failed: {exc}")
            errors += 1

    return {"sent": sent, "errors": errors}


# ---------------------------------------------------------------------------
# APNs (Apple Push Notification service) — stub
# ---------------------------------------------------------------------------

async def send_test_push_notification(
    node_id: str,
    title: str = "OpenClaw",
    body: str = "Test notification",
) -> dict[str, Any]:
    """Send a test push notification via APNs or web push relay.

    Mirrors TS push.test in push.ts.  Full APNs support requires
    environment variables OPENCLAW_APNS_KEY_ID, OPENCLAW_APNS_TEAM_ID,
    OPENCLAW_APNS_BUNDLE_ID, OPENCLAW_APNS_PRIVATE_KEY_P8.
    Falls back gracefully when not configured.
    """
    apns_key_id = os.environ.get("OPENCLAW_APNS_KEY_ID")
    apns_team_id = os.environ.get("OPENCLAW_APNS_TEAM_ID")
    apns_bundle_id = os.environ.get("OPENCLAW_APNS_BUNDLE_ID")
    apns_private_key = os.environ.get("OPENCLAW_APNS_PRIVATE_KEY_P8")

    if not all([apns_key_id, apns_team_id, apns_bundle_id, apns_private_key]):
        # Try web push as fallback
        result = await send_web_push_broadcast(title=title, body=body)
        return {**result, "channel": "web-push"}

    try:
        import httpx  # type: ignore[import-untyped]
        import jwt as pyjwt  # type: ignore[import-untyped]
        import time
    except ImportError:
        logger.warning("httpx/pyjwt not installed — APNs push unavailable")
        return {"ok": False, "reason": "httpx/pyjwt not installed"}

    # Build APNs JWT
    now = int(time.time())
    token_payload = {
        "iss": apns_team_id,
        "iat": now,
    }
    try:
        token = pyjwt.encode(
            token_payload,
            apns_private_key,
            algorithm="ES256",
            headers={"kid": apns_key_id},
        )
        if isinstance(token, bytes):
            token = token.decode("utf-8")

        url = f"https://api.push.apple.com/3/device/{node_id}"
        payload = json.dumps({
            "aps": {"alert": {"title": title, "body": body}, "sound": "default"},
        })
        headers = {
            "authorization": f"bearer {token}",
            "apns-topic": apns_bundle_id,
            "apns-push-type": "alert",
            "content-type": "application/json",
        }
        async with httpx.AsyncClient(http2=True) as client:
            resp = await client.post(url, content=payload, headers=headers)
            if resp.status_code == 200:
                return {"ok": True, "channel": "apns"}
            return {"ok": False, "channel": "apns", "status": resp.status_code, "body": resp.text}
    except Exception as exc:
        logger.warning(f"APNs push failed: {exc}")
        return {"ok": False, "channel": "apns", "error": str(exc)}
