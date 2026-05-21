"""ClawHub skill install/update helpers — mirrors openclaw/src/agents/skills-clawhub.ts."""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, TypedDict

from openclaw.infra.clawhub import (
    download_claw_hub_skill_archive,
    fetch_claw_hub_skill_detail,
    resolve_claw_hub_base_url,
    search_claw_hub_skills,
)

DOT_DIR = ".clawhub"
LEGACY_DOT_DIR = ".clawdhub"
SKILL_MARKERS = ("SKILL.md", "skill.md", "skills.md", "SKILL.MD")
VALID_SLUG_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$", re.IGNORECASE)
NON_ASCII_PATTERN = re.compile(r"[^\x00-\x7F]")


class InstallClawHubSkillOk(TypedDict):
    ok: bool
    slug: str
    version: str
    targetDir: str
    detail: dict[str, Any]


class InstallClawHubSkillErr(TypedDict):
    ok: bool
    error: str


class UpdateClawHubSkillOk(TypedDict):
    ok: bool
    slug: str
    previousVersion: str | None
    version: str
    changed: bool
    targetDir: str


class UpdateClawHubSkillErr(TypedDict):
    ok: bool
    error: str


InstallClawHubSkillResult = InstallClawHubSkillOk | InstallClawHubSkillErr
UpdateClawHubSkillResult = UpdateClawHubSkillOk | UpdateClawHubSkillErr


@dataclass
class ClawHubSkillsLockfile:
    version: int
    skills: dict[str, dict[str, Any]]


def _format_error(err: BaseException) -> str:
    return str(err) or err.__class__.__name__


def normalize_tracked_slug(raw: str) -> str:
    slug = raw.strip()
    if not slug or "/" in slug or "\\" in slug or ".." in slug:
        raise ValueError(f"Invalid skill slug: {raw}")
    return slug


def validate_requested_slug(raw: str) -> str:
    slug = normalize_tracked_slug(raw)
    if NON_ASCII_PATTERN.search(slug) or not VALID_SLUG_PATTERN.fullmatch(slug):
        raise ValueError(f"Invalid skill slug: {raw}")
    return slug


def resolve_skill_install_dir(workspace_dir: str, slug: str) -> Path:
    skills_dir = Path(workspace_dir).resolve() / "skills"
    normalize_tracked_slug(slug)
    target = (skills_dir / slug).resolve()
    try:
        target.relative_to(skills_dir.resolve())
    except ValueError as exc:
        raise ValueError("invalid skill target path") from exc
    return target


def _file_exists(path: Path) -> bool:
    return path.exists()


async def read_claw_hub_skills_lockfile(workspace_dir: str) -> ClawHubSkillsLockfile:
    candidates = [
        Path(workspace_dir) / DOT_DIR / "lock.json",
        Path(workspace_dir) / LEGACY_DOT_DIR / "lock.json",
    ]
    for candidate in candidates:
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            if raw.get("version") == 1 and isinstance(raw.get("skills"), dict):
                return ClawHubSkillsLockfile(version=1, skills=raw["skills"])
        except (OSError, json.JSONDecodeError):
            continue
    return ClawHubSkillsLockfile(version=1, skills={})


async def write_claw_hub_skills_lockfile(
    workspace_dir: str,
    lockfile: ClawHubSkillsLockfile,
) -> None:
    target_path = Path(workspace_dir) / DOT_DIR / "lock.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(
        json.dumps({"version": lockfile.version, "skills": lockfile.skills}, indent=2) + "\n",
        encoding="utf-8",
    )


async def read_claw_hub_skill_origin(skill_dir: Path) -> dict[str, Any] | None:
    candidates = [
        skill_dir / DOT_DIR / "origin.json",
        skill_dir / LEGACY_DOT_DIR / "origin.json",
    ]
    for candidate in candidates:
        try:
            raw = json.loads(candidate.read_text(encoding="utf-8"))
            if (
                raw.get("version") == 1
                and isinstance(raw.get("registry"), str)
                and isinstance(raw.get("slug"), str)
                and isinstance(raw.get("installedVersion"), str)
                and isinstance(raw.get("installedAt"), (int, float))
            ):
                return raw
        except (OSError, json.JSONDecodeError):
            continue
    return None


async def write_claw_hub_skill_origin(skill_dir: Path, origin: dict[str, Any]) -> None:
    target_path = skill_dir / DOT_DIR / "origin.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(origin, indent=2) + "\n", encoding="utf-8")


async def search_skills_from_clawhub(
    *,
    query: str | None = None,
    limit: int | None = None,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    return await search_claw_hub_skills(
        query=query.strip() if query else "*",
        limit=limit,
        base_url=base_url,
    )


def _find_skill_root(extracted: Path) -> Path:
    for marker in SKILL_MARKERS:
        if (extracted / marker).is_file():
            return extracted
    for child in sorted(extracted.iterdir()):
        if child.is_dir():
            for marker in SKILL_MARKERS:
                if (child / marker).is_file():
                    return child
    raise ValueError("downloaded archive is missing SKILL.md")


def _extract_archive_root(archive_path: str) -> Path:
    extract_dir = Path(tempfile.mkdtemp(prefix="openclaw-skill-clawhub-"))
    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            zf.extractall(extract_dir)
        return _find_skill_root(extract_dir)
    except Exception:
        shutil.rmtree(extract_dir, ignore_errors=True)
        raise


async def _install_package_dir(
    *,
    source_dir: Path,
    target_dir: Path,
    mode: str,
) -> dict[str, Any]:
    if mode == "install" and target_dir.exists():
        return {"ok": False, "error": f"Skill already exists at {target_dir}"}
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir)
    return {"ok": True, "targetDir": str(target_dir)}


async def _resolve_install_version(
    *,
    slug: str,
    version: str | None,
    base_url: str | None,
) -> tuple[dict[str, Any], str]:
    detail = await fetch_claw_hub_skill_detail(slug=slug, base_url=base_url)
    if not detail.get("skill"):
        raise ValueError(f'Skill "{slug}" not found on ClawHub.')
    latest = detail.get("latestVersion") or {}
    resolved_version = version or latest.get("version")
    if not resolved_version:
        raise ValueError(f'Skill "{slug}" has no installable version.')
    return detail, str(resolved_version)


async def _perform_claw_hub_skill_install(
    *,
    workspace_dir: str,
    slug: str,
    version: str | None = None,
    base_url: str | None = None,
    force: bool = False,
    logger: Callable[[str], None] | None = None,
) -> InstallClawHubSkillResult:
    try:
        detail, resolved_version = await _resolve_install_version(
            slug=slug,
            version=version,
            base_url=base_url,
        )
        target_dir = resolve_skill_install_dir(workspace_dir, slug)
        if not force and _file_exists(target_dir):
            return {
                "ok": False,
                "error": f"Skill already exists at {target_dir}. Re-run with force/update.",
            }

        if logger:
            logger(f"Downloading {slug}@{resolved_version} from ClawHub…")

        archive = await download_claw_hub_skill_archive(
            slug=slug,
            version=resolved_version,
            base_url=base_url,
        )
        extract_parent: Path | None = None
        try:
            root_dir = _extract_archive_root(archive.archive_path)
            extract_parent = root_dir.parent
            install = await _install_package_dir(
                source_dir=root_dir,
                target_dir=target_dir,
                mode="update" if force else "install",
            )
            if not install.get("ok"):
                return {"ok": False, "error": str(install.get("error") or "failed to install skill")}

            installed_at = int(time.time() * 1000)
            await write_claw_hub_skill_origin(
                target_dir,
                {
                    "version": 1,
                    "registry": resolve_claw_hub_base_url(base_url),
                    "slug": slug,
                    "installedVersion": resolved_version,
                    "installedAt": installed_at,
                },
            )
            lock = await read_claw_hub_skills_lockfile(workspace_dir)
            lock.skills[slug] = {"version": resolved_version, "installedAt": installed_at}
            await write_claw_hub_skills_lockfile(workspace_dir, lock)

            return {
                "ok": True,
                "slug": slug,
                "version": resolved_version,
                "targetDir": str(target_dir),
                "detail": detail,
            }
        finally:
            archive.cleanup()
            if extract_parent is not None:
                shutil.rmtree(extract_parent, ignore_errors=True)
    except Exception as err:
        return {"ok": False, "error": _format_error(err)}


async def install_skill_from_clawhub(
    *,
    workspace_dir: str,
    slug: str,
    version: str | None = None,
    base_url: str | None = None,
    force: bool = False,
    logger: Callable[[str], None] | None = None,
) -> InstallClawHubSkillResult:
    try:
        return await _perform_claw_hub_skill_install(
            workspace_dir=workspace_dir,
            slug=validate_requested_slug(slug),
            version=version,
            base_url=base_url,
            force=force,
            logger=logger,
        )
    except Exception as err:
        return {"ok": False, "error": _format_error(err)}


async def _resolve_requested_update_slug(
    *,
    workspace_dir: str,
    requested_slug: str,
    lock: ClawHubSkillsLockfile,
) -> str:
    tracked_slug = normalize_tracked_slug(requested_slug)
    tracked_target = resolve_skill_install_dir(workspace_dir, tracked_slug)
    tracked_origin = await read_claw_hub_skill_origin(tracked_target)
    if tracked_origin or tracked_slug in lock.skills:
        return tracked_slug
    return validate_requested_slug(requested_slug)


async def _resolve_tracked_update_target(
    *,
    workspace_dir: str,
    slug: str,
    lock: ClawHubSkillsLockfile,
    base_url: str | None,
) -> dict[str, Any]:
    target_dir = resolve_skill_install_dir(workspace_dir, slug)
    origin = await read_claw_hub_skill_origin(target_dir)
    if not origin and slug not in lock.skills:
        return {
            "ok": False,
            "slug": slug,
            "error": f'Skill "{slug}" is not tracked as a ClawHub install.',
        }
    previous = None
    if origin:
        previous = origin.get("installedVersion")
    elif slug in lock.skills:
        previous = lock.skills[slug].get("version")
    return {
        "ok": True,
        "slug": slug,
        "baseUrl": origin.get("registry") if origin else base_url,
        "previousVersion": previous,
    }


async def update_skills_from_clawhub(
    *,
    workspace_dir: str,
    slug: str | None = None,
    base_url: str | None = None,
    logger: Callable[[str], None] | None = None,
) -> list[UpdateClawHubSkillResult]:
    lock = await read_claw_hub_skills_lockfile(workspace_dir)
    if slug:
        slugs = [
            await _resolve_requested_update_slug(
                workspace_dir=workspace_dir,
                requested_slug=slug,
                lock=lock,
            )
        ]
    else:
        slugs = [normalize_tracked_slug(s) for s in lock.skills]

    results: list[UpdateClawHubSkillResult] = []
    for item_slug in slugs:
        tracked = await _resolve_tracked_update_target(
            workspace_dir=workspace_dir,
            slug=item_slug,
            lock=lock,
            base_url=base_url,
        )
        if not tracked.get("ok"):
            results.append({"ok": False, "error": str(tracked.get("error") or "update failed")})
            continue
        install = await _perform_claw_hub_skill_install(
            workspace_dir=workspace_dir,
            slug=str(tracked["slug"]),
            base_url=tracked.get("baseUrl"),
            force=True,
            logger=logger,
        )
        if not install.get("ok"):
            results.append({"ok": False, "error": str(install.get("error") or "update failed")})
            continue
        results.append(
            {
                "ok": True,
                "slug": str(tracked["slug"]),
                "previousVersion": tracked.get("previousVersion"),
                "version": str(install["version"]),
                "changed": tracked.get("previousVersion") != install["version"],
                "targetDir": str(install["targetDir"]),
            }
        )
    return results


async def read_tracked_claw_hub_skill_slugs(workspace_dir: str) -> list[str]:
    lock = await read_claw_hub_skills_lockfile(workspace_dir)
    return sorted(lock.skills.keys())


__all__ = [
    "install_skill_from_clawhub",
    "read_claw_hub_skill_origin",
    "read_claw_hub_skills_lockfile",
    "read_tracked_claw_hub_skill_slugs",
    "search_skills_from_clawhub",
    "update_skills_from_clawhub",
    "validate_requested_slug",
]
