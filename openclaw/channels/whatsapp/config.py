"""WhatsApp channel configuration schema.

Mirrors TypeScript: src/config/zod-schema.providers-whatsapp.ts
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from ...config.paths import resolve_state_dir


# ---------------------------------------------------------------------------
# Nested config objects
# ---------------------------------------------------------------------------

@dataclass
class WhatsAppAckReactionConfig:
    """Immediate acknowledgment reaction on message receipt."""
    emoji: str = ""             # empty string = disabled
    direct: bool = True         # react in DMs (default on)
    group: str = "mentions"     # "always" | "mentions" | "never"

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> WhatsAppAckReactionConfig:
        if not d:
            return cls()
        return cls(
            emoji=d.get("emoji", ""),
            direct=bool(d.get("direct", True)),
            group=d.get("group", "mentions"),
        )


@dataclass
class WhatsAppGroupConfig:
    """Per-group configuration overrides."""
    require_mention: bool | None = None
    tools: dict[str, Any] | None = None
    tools_by_sender: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> WhatsAppGroupConfig:
        if not d:
            return cls()
        return cls(
            require_mention=d.get("requireMention"),
            tools=d.get("tools"),
            tools_by_sender=d.get("toolsBySender"),
        )


@dataclass
class WhatsAppDmConfig:
    """Per-DM user configuration overrides."""
    enabled: bool | None = None
    system_prompt: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> WhatsAppDmConfig:
        if not d:
            return cls()
        return cls(
            enabled=d.get("enabled"),
            system_prompt=d.get("systemPrompt"),
        )


@dataclass
class BlockStreamingCoalesceConfig:
    enabled: bool = False
    min_delay_ms: int = 100
    max_delay_ms: int = 1000

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> BlockStreamingCoalesceConfig:
        if not d:
            return cls()
        return cls(
            enabled=bool(d.get("enabled", False)),
            min_delay_ms=int(d.get("minDelayMs", 100)),
            max_delay_ms=int(d.get("maxDelayMs", 1000)),
        )


@dataclass
class MarkdownConfig:
    mode: str = "native"        # "native" | "escape" | "strip"
    table_mode: str = "native"  # "native" | "ascii" | "simple"

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> MarkdownConfig:
        if not d:
            return cls()
        return cls(
            mode=d.get("mode", "native"),
            table_mode=d.get("tableMode", "native"),
        )


# ---------------------------------------------------------------------------
# Resolved account (merged top-level + per-account)
# ---------------------------------------------------------------------------

@dataclass
class ResolvedWhatsAppAccount:
    """Fully merged per-account config. Mirrors TS ResolvedWhatsAppAccount."""

    account_id: str
    name: str = ""
    auth_dir: str = ""          # overridden Baileys auth directory

    # Access control
    enabled: bool = True
    dm_policy: str = "pairing"          # "pairing" | "allowlist" | "open" | "disabled"
    allow_from: list[str] = field(default_factory=list)  # E.164 list; "*" = open
    default_to: str = ""
    self_chat_mode: bool = False

    # Group settings
    group_policy: str = "allowlist"     # "open" | "allowlist" | "disabled"
    group_allow_from: list[str] = field(default_factory=list)
    groups: dict[str, WhatsAppGroupConfig] = field(default_factory=dict)

    # History
    history_limit: int = 50
    dm_history_limit: int = 0

    # Per-DM config
    dms: dict[str, WhatsAppDmConfig] = field(default_factory=dict)

    # Outbound rendering
    text_chunk_limit: int = 4000
    chunk_mode: str = "length"          # "length" | "newline"
    block_streaming: bool = False
    block_streaming_coalesce: BlockStreamingCoalesceConfig = field(
        default_factory=BlockStreamingCoalesceConfig
    )

    # Media
    media_max_mb: int = 50

    # Features
    send_read_receipts: bool = True
    debounce_ms: int = 0
    ack_reaction: WhatsAppAckReactionConfig = field(default_factory=WhatsAppAckReactionConfig)

    # Messaging config
    message_prefix: str = ""
    response_prefix: str = ""
    capabilities: list[str] = field(default_factory=list)
    config_writes: bool = False
    markdown: MarkdownConfig = field(default_factory=MarkdownConfig)


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

def _coalesce(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


def _str_list(v: Any) -> list[str]:
    if not v:
        return []
    return [str(e).strip() for e in v if str(e).strip()]


def parse_whatsapp_config(cfg: dict[str, Any]) -> list[ResolvedWhatsAppAccount]:
    """
    Parse raw WhatsApp config dict into a list of ResolvedWhatsAppAccount.
    Handles both single-account (top-level creds) and multi-account (accounts map) modes.
    """
    top = cfg
    accounts_dict: dict[str, Any] = top.get("accounts") or {}

    def build(account_id: str, ov: dict[str, Any] | None) -> ResolvedWhatsAppAccount | None:
        o = ov or {}

        enabled = bool(_coalesce(
            o.get("enabled") if "enabled" in o else None,
            top.get("enabled") if "enabled" in top else None,
            True,
        ))
        if not enabled:
            return None

        name = o.get("name") or top.get("name") or account_id

        # Auth dir: per-account override → top-level → default
        raw_auth_dir = (
            o.get("authDir") or o.get("auth_dir") or
            top.get("authDir") or top.get("auth_dir") or ""
        )
        if not raw_auth_dir:
            from pathlib import Path
            raw_auth_dir = str(resolve_state_dir() / "whatsapp" / account_id)

        dm_policy = _coalesce(
            o.get("dmPolicy") or o.get("dm_policy"),
            top.get("dmPolicy") or top.get("dm_policy"),
            "pairing",
        )
        allow_from = _str_list(
            o.get("allowFrom") or o.get("allow_from") or
            top.get("allowFrom") or top.get("allow_from") or []
        )
        default_to = o.get("defaultTo") or o.get("default_to") or top.get("defaultTo") or top.get("default_to") or ""
        self_chat_mode = bool(_coalesce(
            o.get("selfChatMode") if "selfChatMode" in o else None,
            top.get("selfChatMode") if "selfChatMode" in top else None,
            False,
        ))

        group_policy = _coalesce(
            o.get("groupPolicy") or o.get("group_policy"),
            top.get("groupPolicy") or top.get("group_policy"),
            "allowlist",
        )
        group_allow_from = _str_list(
            o.get("groupAllowFrom") or o.get("group_allow_from") or
            top.get("groupAllowFrom") or top.get("group_allow_from") or []
        )

        groups_raw: dict[str, Any] = dict(top.get("groups") or {})
        groups_raw.update(o.get("groups") or {})
        groups = {k: WhatsAppGroupConfig.from_dict(v) for k, v in groups_raw.items()}

        history_limit = int(_coalesce(
            o.get("historyLimit") or o.get("history_limit"),
            top.get("historyLimit") or top.get("history_limit"),
            50,
        ))
        dm_history_limit = int(_coalesce(
            o.get("dmHistoryLimit") or o.get("dm_history_limit"),
            top.get("dmHistoryLimit") or top.get("dm_history_limit"),
            0,
        ))

        dms_raw: dict[str, Any] = dict(top.get("dms") or {})
        dms_raw.update(o.get("dms") or {})
        dms = {k: WhatsAppDmConfig.from_dict(v) for k, v in dms_raw.items()}

        text_chunk_limit = int(_coalesce(
            o.get("textChunkLimit") or o.get("text_chunk_limit"),
            top.get("textChunkLimit") or top.get("text_chunk_limit"),
            4000,
        ))
        chunk_mode = _coalesce(
            o.get("chunkMode") or o.get("chunk_mode"),
            top.get("chunkMode") or top.get("chunk_mode"),
            "length",
        )
        block_streaming = bool(_coalesce(
            o.get("blockStreaming") if "blockStreaming" in o else None,
            top.get("blockStreaming") if "blockStreaming" in top else None,
            False,
        ))

        coalesce_raw = o.get("blockStreamingCoalesce") or top.get("blockStreamingCoalesce")
        block_streaming_coalesce = BlockStreamingCoalesceConfig.from_dict(
            coalesce_raw if isinstance(coalesce_raw, dict) else None
        )

        media_max_mb = int(_coalesce(
            o.get("mediaMaxMb") or o.get("media_max_mb"),
            top.get("mediaMaxMb") or top.get("media_max_mb"),
            50,
        ))

        send_read_receipts = bool(_coalesce(
            o.get("sendReadReceipts") if "sendReadReceipts" in o else None,
            top.get("sendReadReceipts") if "sendReadReceipts" in top else None,
            True,
        ))
        debounce_ms = int(_coalesce(
            o.get("debounceMs") or o.get("debounce_ms"),
            top.get("debounceMs") or top.get("debounce_ms"),
            0,
        ))

        ack_raw = o.get("ackReaction") or top.get("ackReaction")
        ack_reaction = WhatsAppAckReactionConfig.from_dict(
            ack_raw if isinstance(ack_raw, dict) else None
        )

        markdown_raw = o.get("markdown") or top.get("markdown")
        markdown = MarkdownConfig.from_dict(markdown_raw if isinstance(markdown_raw, dict) else None)

        message_prefix = o.get("messagePrefix") or o.get("message_prefix") or top.get("messagePrefix") or ""
        response_prefix = o.get("responsePrefix") or o.get("response_prefix") or top.get("responsePrefix") or ""
        capabilities = list(o.get("capabilities") or top.get("capabilities") or [])
        config_writes = bool(_coalesce(
            o.get("configWrites") if "configWrites" in o else None,
            top.get("configWrites") if "configWrites" in top else None,
            False,
        ))

        return ResolvedWhatsAppAccount(
            account_id=account_id,
            name=name,
            auth_dir=raw_auth_dir,
            enabled=enabled,
            dm_policy=dm_policy,
            allow_from=allow_from,
            default_to=default_to,
            self_chat_mode=self_chat_mode,
            group_policy=group_policy,
            group_allow_from=group_allow_from,
            groups=groups,
            history_limit=history_limit,
            dm_history_limit=dm_history_limit,
            dms=dms,
            text_chunk_limit=text_chunk_limit,
            chunk_mode=chunk_mode,
            block_streaming=block_streaming,
            block_streaming_coalesce=block_streaming_coalesce,
            media_max_mb=media_max_mb,
            send_read_receipts=send_read_receipts,
            debounce_ms=debounce_ms,
            ack_reaction=ack_reaction,
            message_prefix=message_prefix,
            response_prefix=response_prefix,
            capabilities=capabilities,
            config_writes=config_writes,
            markdown=markdown,
        )

    results: list[ResolvedWhatsAppAccount] = []
    if accounts_dict:
        for acct_id, acct_cfg in accounts_dict.items():
            acct = build(acct_id, acct_cfg or {})
            if acct is not None:
                results.append(acct)
    else:
        acct = build("default", {})
        if acct is not None:
            results.append(acct)

    return results
