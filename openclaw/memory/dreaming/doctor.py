"""Doctor RPC helpers for memory dreaming (mirrors TS doctor.ts + dreaming-narrative/repair)."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DREAMS_FILENAMES = ("DREAMS.md", "dreams.md")
DIARY_START_MARKER = "<!-- openclaw:dreaming:diary:start -->"
DIARY_END_MARKER = "<!-- openclaw:dreaming:diary:end -->"
BACKFILL_ENTRY_MARKER = "openclaw:dreaming:backfill-entry"
SHORT_TERM_STORE_RELATIVE_PATH = Path("memory") / ".dreams" / "short-term-recall.json"
SHORT_TERM_PHASE_SIGNAL_RELATIVE_PATH = Path("memory") / ".dreams" / "phase-signals.json"
SESSION_CORPUS_RELATIVE_DIR = Path("memory") / ".dreams" / "session-corpus"
SESSION_INGESTION_RELATIVE_PATH = Path("memory") / ".dreams" / "session-ingestion.json"
REPAIR_ARCHIVE_RELATIVE_DIR = Path(".openclaw-repair") / "dreaming"
DREAMING_NARRATIVE_RUN_PREFIX = "dreaming-narrative-"
DREAMING_NARRATIVE_PROMPT_PREFIX = "Write a dream diary entry from these memory fragments"
DREAMING_ENTRY_LIST_LIMIT = 8

_file_locks: dict[str, tuple[threading.Lock, int]] = {}
_file_locks_guard = threading.Lock()


def _require_absolute_workspace_dir(workspace_dir: str) -> Path:
    trimmed = workspace_dir.strip()
    if not trimmed:
        raise ValueError("workspaceDir is required")
    path = Path(trimmed)
    if not path.is_absolute():
        raise ValueError("workspaceDir must be an absolute path")
    return path.resolve()


def _dreams_lock(path: str) -> threading.Lock:
    with _file_locks_guard:
        entry = _file_locks.get(path)
        if entry is None:
            lock = threading.Lock()
            _file_locks[path] = (lock, 1)
            return lock
        lock, refs = entry
        _file_locks[path] = (lock, refs + 1)
        return lock


def _release_dreams_lock(path: str) -> None:
    with _file_locks_guard:
        entry = _file_locks.get(path)
        if not entry:
            return
        lock, refs = entry
        if refs <= 1:
            _file_locks.pop(path, None)
        else:
            _file_locks[path] = (lock, refs - 1)


async def resolve_dreams_path(workspace_dir: str | Path) -> Path:
    root = Path(workspace_dir)
    for name in DREAMS_FILENAMES:
        candidate = root / name
        if candidate.exists():
            return candidate
    return root / DREAMS_FILENAMES[0]


def _read_dreams_file(dreams_path: Path) -> str:
    try:
        return dreams_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def _ensure_diary_section(existing: str) -> str:
    if DIARY_START_MARKER in existing and DIARY_END_MARKER in existing:
        return existing
    diary_section = f"# Dream Diary\n\n{DIARY_START_MARKER}\n{DIARY_END_MARKER}\n"
    if not existing.strip():
        return diary_section
    return diary_section + "\n" + existing


def _replace_diary_content(existing: str, diary_content: str) -> str:
    ensured = _ensure_diary_section(existing)
    start_idx = ensured.find(DIARY_START_MARKER)
    end_idx = ensured.find(DIARY_END_MARKER)
    if start_idx < 0 or end_idx < start_idx:
        return ensured
    before = ensured[: start_idx + len(DIARY_START_MARKER)]
    after = ensured[end_idx:]
    normalized = f"\n{diary_content.strip()}\n" if diary_content.strip() else "\n"
    return before + normalized + after


def _split_diary_blocks(diary_content: str) -> list[str]:
    return [b.strip() for b in diary_content.split("\n---\n") if b.strip()]


def _join_diary_blocks(blocks: list[str]) -> str:
    if not blocks:
        return ""
    return "".join(f"---\n\n{block.strip()}\n" for block in blocks)


def _normalize_diary_block_fingerprint(block: str) -> str:
    lines = [line.strip() for line in block.split("\n") if line.strip()]
    date_line = ""
    body_lines: list[str] = []
    for line in lines:
        if not date_line and line.startswith("*") and line.endswith("*") and len(line) > 2:
            date_line = line[1:-1].strip()
            continue
        if line.startswith("<!--") or line.startswith("#"):
            continue
        body_lines.append(line)
    normalized_date = " ".join(date_line.split())
    normalized_body = "\n".join(body_lines).strip()
    return f"{normalized_date}\n{normalized_body}"


def _strip_backfill_diary_blocks(existing: str) -> tuple[str, int]:
    ensured = _ensure_diary_section(existing)
    start_idx = ensured.find(DIARY_START_MARKER)
    end_idx = ensured.find(DIARY_END_MARKER)
    if start_idx < 0 or end_idx < start_idx:
        return ensured, 0
    inner = ensured[start_idx + len(DIARY_START_MARKER) : end_idx]
    kept: list[str] = []
    removed = 0
    for block in _split_diary_blocks(inner):
        if BACKFILL_ENTRY_MARKER in block:
            removed += 1
            continue
        kept.append(block)
    return _replace_diary_content(ensured, _join_diary_blocks(kept)), removed


def _write_dreams_file_atomic(dreams_path: Path, content: str) -> None:
    if dreams_path.exists() and dreams_path.is_symlink():
        raise ValueError("Refusing to write symlinked DREAMS.md")
    dreams_path.parent.mkdir(parents=True, exist_ok=True)
    mode = dreams_path.stat().st_mode if dreams_path.exists() else 0o600
    temp_path = dreams_path.with_name(f"{dreams_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(content, encoding="utf-8")
        temp_path.chmod(mode)
        temp_path.replace(dreams_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _resolve_dreams_path_sync(workspace_dir: Path) -> Path:
    for name in DREAMS_FILENAMES:
        candidate = workspace_dir / name
        if candidate.exists():
            return candidate
    return workspace_dir / DREAMS_FILENAMES[0]


def _update_dreams_file_sync(workspace_dir: Path, updater) -> Any:
    dreams_path = _resolve_dreams_path_sync(workspace_dir)
    dreams_path.parent.mkdir(parents=True, exist_ok=True)
    lock = _dreams_lock(str(dreams_path))
    lock.acquire()
    try:
        existing = _read_dreams_file(dreams_path)
        updated = updater(existing, dreams_path)
        content = updated["content"]
        result = updated["result"]
        should_write = updated.get("should_write", True)
        if should_write:
            text = content if content.endswith("\n") else f"{content}\n"
            _write_dreams_file_atomic(dreams_path, text)
        return result
    finally:
        lock.release()
        _release_dreams_lock(str(dreams_path))


async def read_dream_diary(workspace_dir: str) -> dict[str, Any]:
    """Read DREAMS.md dream diary (mirrors TS readDreamDiary)."""
    root = _require_absolute_workspace_dir(workspace_dir)
    for name in DREAMS_FILENAMES:
        file_path = root / name
        try:
            if file_path.is_symlink() or not file_path.is_file():
                continue
            content = file_path.read_text(encoding="utf-8")
            mtime = file_path.stat().st_mtime
            return {
                "found": True,
                "path": name,
                "content": content,
                "updatedAtMs": int(mtime * 1000),
            }
        except OSError:
            return {"found": False, "path": name}
    return {"found": False, "path": DREAMS_FILENAMES[0]}


def format_backfill_diary_date(iso_day: str, _timezone: str | None = None) -> str:
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso_day)
    if not match:
        return iso_day
    year, month, day = match.groups()
    dt = datetime(int(year), int(month), int(day), 12, tzinfo=timezone.utc)
    return dt.strftime("%B %d, %Y").replace(" 0", " ")


def build_backfill_diary_entry(
    iso_day: str,
    body_lines: list[str],
    source_path: str | None = None,
    timezone: str | None = None,
) -> str:
    date_str = format_backfill_diary_date(iso_day, timezone)
    marker = f"<!-- {BACKFILL_ENTRY_MARKER} day={iso_day}"
    if source_path:
        marker += f" source={source_path}"
    marker += " -->"
    body = "\n".join(line.rstrip() for line in body_lines).strip()
    parts = [f"*{date_str}*", marker]
    if body:
        parts.append(body)
    return "\n\n".join(parts)


def extract_iso_day_from_path(file_path: str) -> str | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})\.md$", file_path.replace("\\", "/"), re.I)
    return match.group(1) if match else None


def grounded_markdown_to_diary_lines(markdown: str) -> list[str]:
    lines: list[str] = []
    for line in markdown.split("\n"):
        stripped = re.sub(r"^##\s+", "", line).rstrip()
        if stripped or (lines and lines[-1]):
            lines.append(stripped)
    return [line for line in lines if line or (lines and lines[-1] == "")]


def list_workspace_daily_files(memory_dir: Path) -> list[str]:
    if not memory_dir.is_dir():
        return []
    return sorted(
        str(memory_dir / name)
        for name in memory_dir.iterdir()
        if name.is_file() and re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", name.name, re.I)
    )


def preview_grounded_rem_markdown(
    workspace_dir: str,
    input_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Simplified grounded REM preview for backfill (mirrors previewGroundedRemMarkdown)."""
    root = _require_absolute_workspace_dir(workspace_dir)
    memory_dir = root / "memory"
    paths = input_paths or list_workspace_daily_files(memory_dir)
    files: list[dict[str, Any]] = []
    for rel_or_abs in paths:
        path = Path(rel_or_abs)
        if not path.is_absolute():
            path = memory_dir / path.name if path.name == Path(rel_or_abs).name else root / rel_or_abs
        if not path.exists():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        rel = str(path.relative_to(root)).replace("\\", "/")
        rendered = content.strip()
        files.append({"path": rel, "renderedMarkdown": rendered})
    return {"scannedFiles": len(paths), "files": files}


async def write_backfill_diary_entries(
    workspace_dir: str,
    entries: list[dict[str, Any]],
    timezone: str | None = None,
) -> dict[str, Any]:
    root = _require_absolute_workspace_dir(workspace_dir)

    def updater(existing: str, dreams_path: Path):
        stripped, replaced = _strip_backfill_diary_blocks(existing)
        start_idx = stripped.find(DIARY_START_MARKER)
        end_idx = stripped.find(DIARY_END_MARKER)
        inner = (
            stripped[start_idx + len(DIARY_START_MARKER) : end_idx]
            if start_idx >= 0 and end_idx > start_idx
            else ""
        )
        preserved = _split_diary_blocks(inner)
        next_blocks = [
            *preserved,
            *[
                build_backfill_diary_entry(
                    iso_day=entry["isoDay"],
                    body_lines=entry.get("bodyLines") or [],
                    source_path=entry.get("sourcePath"),
                    timezone=timezone,
                )
                for entry in entries
            ],
        ]
        return {
            "content": _replace_diary_content(stripped, _join_diary_blocks(next_blocks)),
            "result": {
                "path": dreams_path.name,
                "written": len(entries),
                "replaced": replaced,
            },
            "should_write": True,
        }

    result = _update_dreams_file_sync(root, updater)
    return result


async def remove_backfill_diary_entries(workspace_dir: str) -> dict[str, Any]:
    root = _require_absolute_workspace_dir(workspace_dir)

    def updater(existing: str, dreams_path: Path):
        stripped, removed = _strip_backfill_diary_blocks(existing)
        return {
            "content": stripped,
            "result": {"path": dreams_path.name, "removed": removed},
            "should_write": removed > 0 or len(existing) > 0,
        }

    return _update_dreams_file_sync(root, updater)


async def dedupe_dream_diary_entries(workspace_dir: str) -> dict[str, Any]:
    root = _require_absolute_workspace_dir(workspace_dir)

    def updater(existing: str, dreams_path: Path):
        ensured = _ensure_diary_section(existing)
        start_idx = ensured.find(DIARY_START_MARKER)
        end_idx = ensured.find(DIARY_END_MARKER)
        if start_idx < 0 or end_idx < 0 or end_idx < start_idx:
            return {
                "content": ensured,
                "result": {"path": dreams_path.name, "removed": 0, "kept": 0},
                "should_write": False,
            }
        inner = ensured[start_idx + len(DIARY_START_MARKER) : end_idx]
        blocks = _split_diary_blocks(inner)
        seen: set[str] = set()
        kept_blocks: list[str] = []
        removed = 0
        for block in blocks:
            fingerprint = _normalize_diary_block_fingerprint(block)
            if fingerprint in seen:
                removed += 1
                continue
            seen.add(fingerprint)
            kept_blocks.append(block)
        return {
            "content": _replace_diary_content(ensured, _join_diary_blocks(kept_blocks)),
            "result": {
                "path": dreams_path.name,
                "removed": removed,
                "kept": len(kept_blocks),
            },
            "should_write": removed > 0,
        }

    return _update_dreams_file_sync(root, updater)


async def remove_grounded_short_term_candidates(workspace_dir: str) -> dict[str, Any]:
    """Remove grounded-only short-term entries (mirrors TS removeGroundedShortTermCandidates)."""
    root = _require_absolute_workspace_dir(workspace_dir)
    store_path = root / SHORT_TERM_STORE_RELATIVE_PATH
    phase_path = root / SHORT_TERM_PHASE_SIGNAL_RELATIVE_PATH
    removed = 0
    now_iso = datetime.now(timezone.utc).isoformat()

    if not store_path.exists():
        return {"removed": 0, "storePath": str(store_path)}

    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"removed": 0, "storePath": str(store_path)}

    entries = store.get("entries") if isinstance(store.get("entries"), dict) else {}
    for key, value in list(entries.items()):
        if not isinstance(value, dict):
            continue
        grounded = max(0, int(value.get("groundedCount") or 0))
        recall = max(0, int(value.get("recallCount") or 0))
        daily = max(0, int(value.get("dailyCount") or 0))
        if grounded > 0 and recall == 0 and daily == 0:
            del entries[key]
            removed += 1

    if removed > 0:
        store["entries"] = entries
        store["updatedAt"] = now_iso
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(json.dumps(store, indent=2), encoding="utf-8")
        if phase_path.exists():
            try:
                phase_store = json.loads(phase_path.read_text(encoding="utf-8"))
                phase_entries = phase_store.get("entries") if isinstance(phase_store.get("entries"), dict) else {}
                for key in list(phase_entries.keys()):
                    if key not in entries:
                        del phase_entries[key]
                phase_store["entries"] = phase_entries
                phase_store["updatedAt"] = now_iso
                phase_path.write_text(json.dumps(phase_store, indent=2), encoding="utf-8")
            except (OSError, json.JSONDecodeError):
                pass

    return {"removed": removed, "storePath": str(store_path)}


def _is_suspicious_session_corpus_line(line: str) -> bool:
    return (
        DREAMING_NARRATIVE_PROMPT_PREFIX in line
        and (DREAMING_NARRATIVE_RUN_PREFIX in line or "dreaming-narrative-" in line)
    )


async def repair_dreaming_artifacts(
    workspace_dir: str,
    *,
    archive_diary: bool = False,
) -> dict[str, Any]:
    """Archive corrupted dreaming artifacts (mirrors TS repairDreamingArtifacts)."""
    root = _require_absolute_workspace_dir(workspace_dir)
    warnings: list[str] = []
    archived_paths: list[str] = []
    archive_dir: Path | None = None
    archived_dreams_diary = False
    archived_session_corpus = False
    archived_session_ingestion = False

    def ensure_archive_dir() -> Path:
        nonlocal archive_dir
        if archive_dir is None:
            stamp = datetime.now(timezone.utc).isoformat().replace(":", "-").replace(".", "-")
            archive_dir = root / REPAIR_ARCHIVE_RELATIVE_DIR / stamp
            archive_dir.mkdir(parents=True, exist_ok=True)
        return archive_dir

    def archive_path_if_present(target: Path) -> str | None:
        if not target.exists() or target.is_symlink():
            return None
        try:
            dest_dir = ensure_archive_dir()
            dest = dest_dir / f"{target.name}.{uuid.uuid4().hex}"
            target.rename(dest)
            return str(dest)
        except OSError as exc:
            warnings.append(str(exc))
            return None

    corpus_dir = root / SESSION_CORPUS_RELATIVE_DIR
    suspicious = False
    if corpus_dir.is_dir():
        for corpus_file in corpus_dir.glob("*.txt"):
            try:
                content = corpus_file.read_text(encoding="utf-8")
            except OSError:
                continue
            for line in content.splitlines():
                if line.strip() and _is_suspicious_session_corpus_line(line):
                    suspicious = True
                    break
            if suspicious:
                break

    if suspicious:
        dest = archive_path_if_present(corpus_dir)
        if dest:
            archived_session_corpus = True
            archived_paths.append(dest)

    ingestion_path = root / SESSION_INGESTION_RELATIVE_PATH
    dest = archive_path_if_present(ingestion_path)
    if dest:
        archived_session_ingestion = True
        archived_paths.append(dest)

    if archive_diary:
        for name in DREAMS_FILENAMES:
            dreams_path = root / name
            if dreams_path.is_file() and not dreams_path.is_symlink():
                dest = archive_path_if_present(dreams_path)
                if dest:
                    archived_dreams_diary = True
                    archived_paths.append(dest)
                break

    changed = archived_dreams_diary or archived_session_corpus or archived_session_ingestion
    payload: dict[str, Any] = {
        "changed": changed,
        "archivedDreamsDiary": archived_dreams_diary,
        "archivedSessionCorpus": archived_session_corpus,
        "archivedSessionIngestion": archived_session_ingestion,
        "archivedPaths": archived_paths,
        "warnings": warnings,
    }
    if archive_dir is not None:
        payload["archiveDir"] = str(archive_dir)
    return payload


def _to_non_negative_int(value: Any) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, num)


def _is_short_term_memory_path(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/").lstrip("./")
    if re.search(r"(?:^|/)memory/(\d{4})-(\d{2})-(\d{2})\.md$", normalized):
        return True
    if re.search(
        r"(?:^|/)memory/\.dreams/session-corpus/(\d{4})-(\d{2})-(\d{2})\.(?:md|txt)$",
        normalized,
    ):
        return True
    return bool(re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})\.md", normalized, re.I))


async def load_dreaming_store_stats(
    workspace_dir: str,
    now_ms: int | None = None,
    timezone: str | None = None,
) -> dict[str, Any]:
    """Load short-term dreaming store stats for doctor.memory.status."""
    root = _require_absolute_workspace_dir(workspace_dir)
    store_path = root / SHORT_TERM_STORE_RELATIVE_PATH
    phase_signal_path = root / SHORT_TERM_PHASE_SIGNAL_RELATIVE_PATH
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)

    empty = {
        "shortTermCount": 0,
        "recallSignalCount": 0,
        "dailySignalCount": 0,
        "groundedSignalCount": 0,
        "totalSignalCount": 0,
        "phaseSignalCount": 0,
        "lightPhaseHitCount": 0,
        "remPhaseHitCount": 0,
        "promotedTotal": 0,
        "promotedToday": 0,
        "shortTermEntries": [],
        "signalEntries": [],
        "promotedEntries": [],
        "storePath": str(store_path),
        "phaseSignalPath": str(phase_signal_path),
    }

    if not store_path.exists():
        return empty

    try:
        store = json.loads(store_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {**empty, "storeError": str(exc)}

    entries = store.get("entries") if isinstance(store.get("entries"), dict) else {}
    stats = dict(empty)
    active_keys: set[str] = set()
    short_term_entries: list[dict[str, Any]] = []
    promoted_entries: list[dict[str, Any]] = []
    active_entries: dict[str, dict[str, Any]] = {}

    for entry_key, value in entries.items():
        if not isinstance(value, dict):
            continue
        source = str(value.get("source") or "").strip()
        entry_path = str(value.get("path") or "").strip()
        if source != "memory" or not entry_path or not _is_short_term_memory_path(entry_path):
            continue
        recall = _to_non_negative_int(value.get("recallCount"))
        daily = _to_non_negative_int(value.get("dailyCount"))
        grounded = _to_non_negative_int(value.get("groundedCount"))
        total_entry = recall + daily + grounded
        detail = {
            "key": entry_key,
            "path": entry_path.replace("\\", "/"),
            "startLine": max(1, _to_non_negative_int(value.get("startLine")) or 1),
            "endLine": max(1, _to_non_negative_int(value.get("endLine")) or 1),
            "snippet": str(value.get("snippet") or value.get("summary") or entry_path).strip(),
            "recallCount": recall,
            "dailyCount": daily,
            "groundedCount": grounded,
            "totalSignalCount": total_entry,
            "lightHits": 0,
            "remHits": 0,
            "phaseHitCount": 0,
        }
        if value.get("lastRecalledAt"):
            detail["lastRecalledAt"] = value["lastRecalledAt"]
        promoted_at = value.get("promotedAt")
        if not promoted_at:
            stats["shortTermCount"] += 1
            active_keys.add(entry_key)
            stats["recallSignalCount"] += recall
            stats["dailySignalCount"] += daily
            stats["groundedSignalCount"] += grounded
            stats["totalSignalCount"] += total_entry
            short_term_entries.append(detail)
            active_entries[entry_key] = detail
            continue
        stats["promotedTotal"] += 1
        promoted_entries.append({**detail, "promotedAt": promoted_at})

    if phase_signal_path.exists():
        try:
            phase_store = json.loads(phase_signal_path.read_text(encoding="utf-8"))
            phase_entries = phase_store.get("entries") if isinstance(phase_store.get("entries"), dict) else {}
            for key, phase_value in phase_entries.items():
                if key not in active_keys or not isinstance(phase_value, dict):
                    continue
                light_hits = _to_non_negative_int(phase_value.get("lightHits"))
                rem_hits = _to_non_negative_int(phase_value.get("remHits"))
                stats["lightPhaseHitCount"] += light_hits
                stats["remPhaseHitCount"] += rem_hits
                stats["phaseSignalCount"] += light_hits + rem_hits
                detail = active_entries.get(key)
                if detail:
                    detail["lightHits"] = light_hits
                    detail["remHits"] = rem_hits
                    detail["phaseHitCount"] = light_hits + rem_hits
        except (OSError, json.JSONDecodeError) as exc:
            stats["phaseSignalError"] = str(exc)

    short_term_entries.sort(
        key=lambda e: (-e.get("totalSignalCount", 0), e.get("path", "")),
    )
    stats["shortTermEntries"] = short_term_entries[:DREAMING_ENTRY_LIST_LIMIT]
    stats["promotedEntries"] = promoted_entries[:DREAMING_ENTRY_LIST_LIMIT]
    stats["signalEntries"] = sorted(
        short_term_entries,
        key=lambda e: (-e.get("phaseHitCount", 0), -e.get("totalSignalCount", 0)),
    )[:DREAMING_ENTRY_LIST_LIMIT]
    return stats


async def resolve_all_managed_dreaming_cron_statuses(workspace_dir: str) -> dict[str, bool]:
    """Check cron service for managed dreaming jobs per phase.

    Returns dict of {light: bool, deep: bool, rem: bool}.
    """
    result = {"light": False, "deep": False, "rem": False}
    try:
        from openclaw.cron.service import get_cron_service
        svc = get_cron_service()
        if svc is None:
            return result
        if hasattr(svc, "list_jobs") and callable(svc.list_jobs):
            import inspect
            raw = svc.list_jobs()
            if inspect.isawaitable(raw):
                jobs = await raw
            else:
                jobs = raw
        else:
            jobs = []
        for job in jobs:
            job_id = str(getattr(job, "id", "") or (job.get("id", "") if isinstance(job, dict) else ""))
            payload = getattr(job, "payload", None) or (job.get("payload", {}) if isinstance(job, dict) else {})
            if isinstance(payload, dict):
                p_kind = payload.get("kind", "")
            else:
                p_kind = getattr(payload, "kind", "")
            if p_kind != "systemEvent":
                continue
            event_type = payload.get("type", "") if isinstance(payload, dict) else getattr(payload, "type", "")
            for phase in ("light", "deep", "rem"):
                if phase in event_type.lower() or phase in job_id.lower():
                    result[phase] = True
    except Exception:
        pass
    return result


async def build_dreaming_status_payload(cfg: Any, store_stats: dict[str, Any]) -> dict[str, Any]:
    """Build dreaming section for doctor.memory.status."""
    from .config import resolve_dreaming_config

    resolved = resolve_dreaming_config(cfg)
    phases = resolved.phases

    # Attempt to resolve real managed cron statuses
    workspace_dir = store_stats.get("workspaceDir", "")
    managed_cron = await resolve_all_managed_dreaming_cron_statuses(str(workspace_dir))

    base = {
        "enabled": resolved.enabled,
        "verboseLogging": resolved.verbose_logging,
        "storageMode": resolved.storage.mode,
        "separateReports": resolved.storage.separate_reports,
        "shortTermEntries": store_stats.get("shortTermEntries") or [],
        "signalEntries": store_stats.get("signalEntries") or [],
        "promotedEntries": store_stats.get("promotedEntries") or [],
        "phases": {
            "light": {
                "enabled": phases.light.enabled,
                "cron": phases.light.cron,
                "lookbackDays": phases.light.lookback_days,
                "limit": phases.light.limit,
                "managedCronPresent": managed_cron["light"],
            },
            "deep": {
                "enabled": phases.deep.enabled,
                "cron": phases.deep.cron,
                "limit": phases.deep.limit,
                "minScore": phases.deep.min_score,
                "minRecallCount": phases.deep.min_recall_count,
                "minUniqueQueries": phases.deep.min_unique_queries,
                "recencyHalfLifeDays": phases.deep.recency_half_life_days,
                "managedCronPresent": managed_cron["deep"],
                **({"maxAgeDays": phases.deep.max_age_days} if phases.deep.max_age_days is not None else {}),
            },
            "rem": {
                "enabled": phases.rem.enabled,
                "cron": phases.rem.cron,
                "lookbackDays": phases.rem.lookback_days,
                "limit": phases.rem.limit,
                "minPatternStrength": phases.rem.min_pattern_strength,
                "managedCronPresent": managed_cron["rem"],
            },
        },
    }
    if resolved.timezone:
        base["timezone"] = resolved.timezone
    for key in (
        "shortTermCount",
        "recallSignalCount",
        "dailySignalCount",
        "groundedSignalCount",
        "totalSignalCount",
        "phaseSignalCount",
        "lightPhaseHitCount",
        "remPhaseHitCount",
        "promotedTotal",
        "promotedToday",
        "storePath",
        "phaseSignalPath",
        "storeError",
        "phaseSignalError",
        "lastPromotedAt",
    ):
        if key in store_stats:
            base[key] = store_stats[key]
    return base
