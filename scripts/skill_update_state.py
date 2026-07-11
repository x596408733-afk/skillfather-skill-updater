#!/usr/bin/env python3
"""Deterministic state operations for skillfather-skill-updater."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit


SCHEMA_VERSION = 2
STATUS_MAP = {
    "无更新": "no_update",
    "待更新": "update_available",
    "未登记地址": "unregistered",
    "检查失败": "check_failed",
}
VALID_STATUSES = {
    "unregistered",
    "no_update",
    "update_available",
    "review_required",
    "conflict",
    "check_failed",
}


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def normalize_hash(value):
    if value is None:
        return None
    if re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return f"sha256:{value.lower()}"
    if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", value):
        return value.lower()
    return value


def extract_skill_version(path):
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        match = re.fullmatch(r"version:(.*)", line)
        if not match:
            continue
        scalar = match.group(1)
        if re.fullmatch(r"[ \t]+#.*", scalar):
            return None
        scalar = scalar.lstrip()
        if scalar.startswith('"'):
            quoted = re.fullmatch(r'"([^\"]+)"(?:[ \t]+#.*)?[ \t]*', scalar)
            return quoted.group(1) if quoted else None
        if scalar.startswith("'"):
            quoted = re.fullmatch(r"'([^']+)'(?:[ \t]+#.*)?[ \t]*", scalar)
            return quoted.group(1) if quoted else None
        value = re.sub(r"[ \t]+#.*$", "", scalar).strip()
        return value or None
    return None


def display_version(path, accepted_commit=None, accepted_hash=None):
    version = extract_skill_version(path)
    if version:
        return version
    actual_hash = sha256_file(path)
    if accepted_commit and actual_hash == normalize_hash(accepted_hash):
        return accepted_commit[:12]
    return actual_hash.split(":", 1)[1][:12]


def empty_registry():
    return {"schema_version": SCHEMA_VERSION, "skills": []}


def _migrate_entry(source, force_first_review=False):
    entry = dict(source)
    entry["status"] = STATUS_MAP.get(entry.get("status"), entry.get("status", "unregistered"))
    entry["base_hash"] = normalize_hash(entry.get("base_hash"))
    entry.setdefault("base_commit_sha", None)
    latest_hash = normalize_hash(entry.pop("latest_hash", None))
    entry["candidate_hash"] = normalize_hash(entry.get("candidate_hash")) or latest_hash
    entry.setdefault("candidate_snapshot", None)
    entry.setdefault("candidate_commit_sha", None)
    entry.setdefault("local_hash_at_check", None)
    entry.setdefault("approved_candidate_hash", None)
    entry.setdefault("approved_local_hash", None)
    entry.setdefault("approved_at", None)
    if force_first_review:
        entry["first_diff_required"] = True
    else:
        entry.setdefault("first_diff_required", not bool(entry.get("base_snapshot")))
    entry.setdefault("last_error", None)
    entry.setdefault("pending_conflicts", [])
    return entry


def load_registry(path):
    registry_path = Path(path)
    if not registry_path.exists():
        return empty_registry(), True

    data = json.loads(registry_path.read_text(encoding="utf-8-sig"))
    changed = False
    force_first_review = False
    if isinstance(data, list):
        data = {"schema_version": SCHEMA_VERSION, "skills": data}
        changed = True
        force_first_review = True
    elif not isinstance(data, dict):
        raise ValueError("registry root must be an object or legacy array")

    schema_version = data.get("schema_version")
    if isinstance(schema_version, int) and schema_version > SCHEMA_VERSION:
        raise ValueError(f"newer registry schema {schema_version} is not supported")
    if schema_version not in (None, 1, SCHEMA_VERSION):
        raise ValueError(f"unsupported registry schema: {schema_version!r}")

    if "skills" not in data and "entries" in data:
        data["skills"] = data.pop("entries")
        changed = True
    if not isinstance(data.get("skills"), list):
        raise ValueError("registry.skills must be an array")
    if data.get("schema_version") != SCHEMA_VERSION:
        data["schema_version"] = SCHEMA_VERSION
        changed = True
        force_first_review = True

    migrated = [_migrate_entry(entry, force_first_review) for entry in data["skills"]]
    if migrated != data["skills"]:
        data["skills"] = migrated
        changed = True
    validate_registry(data)
    return data, changed


def load_registry_for_write(path, allow_create=False):
    registry_path = Path(path)
    existed = registry_path.exists()
    data, changed = load_registry(registry_path)
    if changed and existed:
        raise ValueError("registry migration required; run migrate before changing state")
    if not existed and not allow_create:
        raise FileNotFoundError(f"registry does not exist: {registry_path}")
    return data


def validate_registry(data):
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    skills = data.get("skills")
    if not isinstance(skills, list):
        raise ValueError("registry.skills must be an array")

    names = set()
    for entry in skills:
        name = entry.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9-]+", name):
            raise ValueError(f"invalid skill name: {name!r}")
        if name in names:
            raise ValueError(f"duplicate skill name: {name}")
        names.add(name)
        if entry.get("status") not in VALID_STATUSES:
            raise ValueError(f"invalid status for {name}: {entry.get('status')}")
        if not isinstance(entry.get("pending_conflicts", []), list):
            raise ValueError(f"pending_conflicts must be an array for {name}")
    return True


def _atomic_write_bytes(path, content):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_copy(source, destination):
    _atomic_write_bytes(destination, Path(source).read_bytes())


def save_registry(path, data):
    validate_registry(data)
    encoded = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, encoded)


def _find_entry(data, name):
    for entry in data["skills"]:
        if entry["name"] == name:
            return entry
    raise KeyError(f"skill is not registered: {name}")


def _snapshot_path(registry_path, name, kind, content_hash):
    if not re.fullmatch(r"[a-z0-9-]+", name):
        raise ValueError(f"invalid skill name: {name}")
    short_hash = content_hash.split(":", 1)[-1][:12]
    return Path(registry_path).parent / "skill-update-snapshots" / name / f"{kind}-{short_hash}.md"


def _relative_to_registry(registry_path, snapshot_path):
    return Path(snapshot_path).relative_to(Path(registry_path).parent).as_posix()


def stage_candidate(
    registry_path,
    name,
    local_path,
    upstream_url,
    upstream_ref,
    candidate_path,
    commit_sha=None,
    latest_version=None,
):
    registry_path = Path(registry_path)
    local_path = Path(local_path).resolve()
    candidate_path = Path(candidate_path).resolve()
    if local_path.name != "SKILL.md":
        raise ValueError("local path must point to SKILL.md")
    if not local_path.is_file():
        raise FileNotFoundError(f"local Skill file does not exist: {local_path}")
    if not candidate_path.is_file():
        raise FileNotFoundError(f"candidate file does not exist: {candidate_path}")
    if not commit_sha or not re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha):
        raise ValueError("commit_sha must be a 40-character hexadecimal SHA")
    build_pinned_raw_url(upstream_url, upstream_ref, commit_sha)

    data = load_registry_for_write(registry_path, allow_create=True)
    existing = next((item for item in data["skills"] if item["name"] == name), None)
    if existing:
        identity_changed = (
            Path(existing["local_path"]).resolve() != local_path
            or existing.get("upstream_url") != upstream_url
            or existing.get("upstream_ref") != upstream_ref
        )
        if identity_changed:
            raise ValueError("registered identity changed; create a new registration")
    if existing and any(c.get("status", "unresolved") == "unresolved" for c in existing["pending_conflicts"]):
        old_candidate = existing.get("candidate_hash")
        new_candidate = sha256_file(candidate_path)
        if old_candidate and old_candidate != new_candidate:
            raise ValueError("resolve existing conflicts before staging a new candidate")

    local_hash = sha256_file(local_path)
    candidate_hash = sha256_file(candidate_path)
    candidate_version = (
        latest_version
        if latest_version is not None and latest_version.strip()
        else extract_skill_version(candidate_path) or commit_sha.lower()[:12]
    )
    candidate_snapshot = _snapshot_path(registry_path, name, "candidate", candidate_hash)
    atomic_copy(candidate_path, candidate_snapshot)

    if existing is None:
        entry = {
            "name": name,
            "local_version": display_version(local_path),
            "pending_conflicts": [],
        }
        data["skills"].append(entry)
        if local_hash == candidate_hash:
            base_snapshot = _snapshot_path(registry_path, name, "base", candidate_hash)
            atomic_copy(candidate_path, base_snapshot)
            entry["base_snapshot"] = _relative_to_registry(registry_path, base_snapshot)
            entry["base_hash"] = candidate_hash
            entry["base_commit_sha"] = commit_sha.lower()
            entry["first_diff_required"] = False
            status = "no_update"
        else:
            entry["base_snapshot"] = None
            entry["base_hash"] = None
            entry["base_commit_sha"] = None
            entry["first_diff_required"] = True
            status = "review_required"
    else:
        entry = existing
        base_hash = normalize_hash(entry.get("base_hash"))
        unresolved = [
            item
            for item in entry["pending_conflicts"]
            if item.get("status", "unresolved") == "unresolved"
        ]
        if unresolved:
            status = "conflict"
        elif entry.get("first_diff_required") or not base_hash:
            status = "review_required"
        elif candidate_hash == base_hash:
            status = "no_update"
        else:
            status = "update_available"

    entry.update(
        {
            "local_path": str(local_path),
            "upstream_url": upstream_url,
            "upstream_ref": upstream_ref,
            "candidate_snapshot": _relative_to_registry(registry_path, candidate_snapshot),
            "candidate_hash": candidate_hash,
            "candidate_commit_sha": commit_sha.lower() if commit_sha else None,
            "local_hash_at_check": local_hash,
            "latest_version": candidate_version,
            "status": status,
            "last_checked_at": utc_now(),
            "last_error": None,
            "approved_candidate_hash": None,
            "approved_local_hash": None,
            "approved_at": None,
        }
    )
    save_registry(registry_path, data)
    return dict(entry)


def add_conflict(registry_path, name, conflict_id, section, base_hash, local_hash, candidate_hash):
    data = load_registry_for_write(registry_path)
    entry = _find_entry(data, name)
    if candidate_hash != entry.get("candidate_hash"):
        raise ValueError("conflict does not match the pinned candidate")
    if any(item.get("id") == conflict_id for item in entry["pending_conflicts"]):
        raise ValueError(f"duplicate conflict id: {conflict_id}")
    entry["pending_conflicts"].append(
        {
            "id": conflict_id,
            "section": section,
            "base_hash": normalize_hash(base_hash),
            "local_hash": normalize_hash(local_hash),
            "candidate_hash": normalize_hash(candidate_hash),
            "status": "unresolved",
            "resolution": None,
            "created_at": utc_now(),
        }
    )
    entry["status"] = "conflict"
    entry["approved_candidate_hash"] = None
    entry["approved_local_hash"] = None
    entry["approved_at"] = None
    save_registry(registry_path, data)
    return dict(entry)


def resolve_conflict(registry_path, name, conflict_id, resolution):
    if resolution not in {"keep-local", "use-upstream", "manual-merge", "dismiss"}:
        raise ValueError("invalid conflict resolution")
    data = load_registry_for_write(registry_path)
    entry = _find_entry(data, name)
    conflict = next((item for item in entry["pending_conflicts"] if item.get("id") == conflict_id), None)
    if conflict is None:
        raise KeyError(f"conflict not found: {conflict_id}")
    conflict["status"] = "resolved"
    conflict["resolution"] = resolution
    conflict["resolved_at"] = utc_now()
    has_unresolved = any(
        item.get("status", "unresolved") == "unresolved"
        for item in entry["pending_conflicts"]
    )
    entry["status"] = "conflict" if has_unresolved else "review_required"
    entry["approved_candidate_hash"] = None
    entry["approved_local_hash"] = None
    entry["approved_at"] = None
    save_registry(registry_path, data)
    return dict(entry)


def approve_candidate(registry_path, name, candidate_hash, local_hash):
    data = load_registry_for_write(registry_path)
    entry = _find_entry(data, name)
    candidate_hash = normalize_hash(candidate_hash)
    local_hash = normalize_hash(local_hash)
    if candidate_hash != entry.get("candidate_hash"):
        raise ValueError("candidate hash changed; run diff again")
    unresolved = [
        item for item in entry["pending_conflicts"] if item.get("status", "unresolved") == "unresolved"
    ]
    if unresolved:
        raise ValueError("cannot approve with unresolved conflicts")
    actual_local_hash = sha256_file(entry["local_path"])
    if actual_local_hash != local_hash:
        raise ValueError("local hash does not match the approved merge result")
    entry["approved_candidate_hash"] = candidate_hash
    entry["approved_local_hash"] = local_hash
    entry["approved_at"] = utc_now()
    entry["status"] = "review_required"
    save_registry(registry_path, data)
    return dict(entry)


def finalize_candidate(registry_path, name, candidate_hash):
    data = load_registry_for_write(registry_path)
    entry = _find_entry(data, name)
    candidate_hash = normalize_hash(candidate_hash)
    if candidate_hash != entry.get("candidate_hash"):
        raise ValueError("candidate hash changed; run diff again")
    unresolved = [
        item for item in entry["pending_conflicts"] if item.get("status", "unresolved") == "unresolved"
    ]
    if unresolved:
        raise ValueError("cannot finalize with unresolved conflicts")
    if entry.get("approved_candidate_hash") != candidate_hash or not entry.get("approved_local_hash"):
        raise ValueError("candidate approval required before finalization")
    if sha256_file(entry["local_path"]) != entry["approved_local_hash"]:
        raise ValueError("local file changed after approval")
    candidate_snapshot = Path(registry_path).parent / entry["candidate_snapshot"]
    if sha256_file(candidate_snapshot) != candidate_hash:
        raise ValueError("candidate snapshot hash mismatch")
    entry["local_version"] = display_version(
        entry["local_path"],
        accepted_commit=entry.get("candidate_commit_sha"),
        accepted_hash=entry.get("candidate_hash"),
    )
    entry["latest_version"] = (
        extract_skill_version(candidate_snapshot)
        or entry["candidate_commit_sha"][:12]
    )
    base_snapshot = _snapshot_path(registry_path, name, "base", candidate_hash)
    atomic_copy(candidate_snapshot, base_snapshot)
    entry["base_snapshot"] = _relative_to_registry(registry_path, base_snapshot)
    entry["base_hash"] = candidate_hash
    entry["base_commit_sha"] = entry.get("candidate_commit_sha")
    entry["first_diff_required"] = False
    entry["pending_conflicts"] = []
    entry["status"] = "no_update"
    entry["last_error"] = None
    entry["approved_candidate_hash"] = None
    entry["approved_local_hash"] = None
    entry["approved_at"] = None
    save_registry(registry_path, data)
    return dict(entry)


def create_backup(registry_path, name):
    data, _ = load_registry(registry_path)
    entry = _find_entry(data, name)
    local_path = Path(entry["local_path"])
    if not local_path.is_file():
        raise FileNotFoundError(f"local Skill file does not exist: {local_path}")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = local_path.parent / ".skill-update-backups" / f"{local_path.name}.{stamp}.bak"
    atomic_copy(local_path, backup_path)
    return backup_path


def restore_backup(registry_path, name, backup_path):
    data = load_registry_for_write(registry_path)
    entry = _find_entry(data, name)
    local_path = Path(entry["local_path"])
    backup_path = Path(backup_path).resolve()
    managed_backup_dir = (local_path.parent / ".skill-update-backups").resolve()
    try:
        backup_path.relative_to(managed_backup_dir)
    except ValueError as error:
        raise ValueError("backup must be inside the managed backup directory") from error
    if not backup_path.is_file():
        raise FileNotFoundError(f"backup does not exist: {backup_path}")
    pre_restore = create_backup(registry_path, name)
    atomic_copy(backup_path, local_path)
    entry["local_hash_at_check"] = sha256_file(local_path)
    entry["status"] = "review_required"
    entry["last_error"] = None
    entry["approved_candidate_hash"] = None
    entry["approved_local_hash"] = None
    entry["approved_at"] = None
    save_registry(registry_path, data)
    return pre_restore


def mark_failure(registry_path, name, error_message):
    data = load_registry_for_write(registry_path)
    entry = _find_entry(data, name)
    entry["status"] = "check_failed"
    entry["last_error"] = str(error_message)
    entry["last_checked_at"] = utc_now()
    entry["approved_candidate_hash"] = None
    entry["approved_local_hash"] = None
    entry["approved_at"] = None
    save_registry(registry_path, data)
    return dict(entry)


def list_entries(registry_path):
    data, _ = load_registry(registry_path)
    return [dict(entry) for entry in sorted(data["skills"], key=lambda item: item["name"])]


def fast_eligibility(registry_path, name):
    data, _ = load_registry(registry_path)
    entry = _find_entry(data, name)
    unresolved = any(
        item.get("status", "unresolved") == "unresolved"
        for item in entry.get("pending_conflicts", [])
    )
    checks = (
        (entry.get("first_diff_required", True), "first_review_required"),
        (unresolved, "unresolved_conflicts"),
        (not entry.get("base_hash"), "missing_base"),
        (not entry.get("candidate_snapshot"), "missing_candidate"),
    )
    for failed, reason in checks:
        if failed:
            return {"eligible": False, "reason": reason, "name": name}

    candidate_snapshot = Path(registry_path).parent / entry["candidate_snapshot"]
    if not candidate_snapshot.is_file():
        return {"eligible": False, "reason": "missing_candidate", "name": name}
    if sha256_file(candidate_snapshot) != entry.get("candidate_hash"):
        return {
            "eligible": False,
            "reason": "candidate_snapshot_hash_mismatch",
            "name": name,
        }

    local_hash = sha256_file(entry["local_path"])
    if local_hash != entry["base_hash"]:
        return {
            "eligible": False,
            "reason": "local_differs_from_base",
            "name": name,
        }
    if entry.get("candidate_hash") == entry.get("base_hash"):
        return {"eligible": False, "reason": "no_update", "name": name}
    return {
        "eligible": True,
        "reason": "eligible",
        "name": name,
        "candidate_hash": entry["candidate_hash"],
        "local_hash": local_hash,
    }


def fast_apply(registry_path, name, candidate_hash):
    data, _ = load_registry(registry_path)
    entry = _find_entry(data, name)
    candidate_hash = normalize_hash(candidate_hash)
    if candidate_hash != entry.get("candidate_hash"):
        raise ValueError("candidate hash changed; run check again")

    eligibility = fast_eligibility(registry_path, name)
    if not eligibility["eligible"]:
        reason = eligibility["reason"].replace("_", " ")
        raise ValueError(f"fast update not eligible: {reason}")

    candidate_snapshot = Path(registry_path).parent / entry["candidate_snapshot"]
    if sha256_file(candidate_snapshot) != candidate_hash:
        raise ValueError("candidate snapshot hash mismatch")
    backup_path = create_backup(registry_path, name)
    if sha256_file(entry["local_path"]) != eligibility["local_hash"]:
        raise ValueError("local file changed after fast eligibility check")

    atomic_copy(candidate_snapshot, entry["local_path"])
    final_local_hash = sha256_file(entry["local_path"])
    approve_candidate(registry_path, name, candidate_hash, final_local_hash)
    finalized = finalize_candidate(registry_path, name, candidate_hash)
    return {"backup_path": str(backup_path), "entry": finalized}


def skill_name(path):
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    if lines and lines[0].strip() == "---":
        for line in lines[1:]:
            if line.strip() == "---":
                break
            match = re.fullmatch(
                r"name:\s*(?:\"([^\"]+)\"|'([^']+)'|([^#]+?))\s*(?:#.*)?",
                line,
            )
            if match:
                return next(
                    value.strip() for value in match.groups() if value is not None
                )
    return Path(path).parent.name


def discover_skill_files(codex_home, plugin_skills=()):
    codex_home = Path(codex_home).resolve()
    skill_root = codex_home / "skills"
    candidates = list(skill_root.glob("*/SKILL.md"))
    candidates.extend((skill_root / ".system").glob("*/SKILL.md"))
    candidates.extend(Path(path) for path in plugin_skills)
    if not plugin_skills:
        plugin_cache = codex_home / "plugins" / "cache"
        if plugin_cache.is_dir():
            candidates.extend(plugin_cache.rglob("SKILL.md"))

    found = {}
    for path in candidates:
        if path.is_file():
            resolved = path.resolve()
            found[os.path.normcase(str(resolved))] = resolved
    return [found[key] for key in sorted(found)]


def skill_type(path, codex_home, plugin_skills=()):
    path = Path(path).resolve()
    codex_home = Path(codex_home).resolve()
    normalized_plugins = {
        os.path.normcase(str(Path(item).resolve())) for item in plugin_skills
    }
    if os.path.normcase(str(path)) in normalized_plugins:
        return "plugin"
    for root, kind in (
        (codex_home / "skills" / ".system", "system"),
        (codex_home / "plugins" / "cache", "plugin"),
    ):
        try:
            path.relative_to(root)
            return kind
        except ValueError:
            pass
    return "personal"


def _git_output(directory, *arguments):
    completed = subprocess.run(
        ["git", "-C", str(directory), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def infer_github_blob_url(skill_path):
    skill_path = Path(skill_path).resolve()
    try:
        root = Path(
            _git_output(skill_path.parent, "rev-parse", "--show-toplevel")
        ).resolve()
        remote = _git_output(root, "config", "--get", "remote.origin.url")
        match = re.fullmatch(
            r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?", remote
        )
        if not match:
            return None
        try:
            branch = _git_output(
                root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD"
            )
            branch = branch[7:] if branch.startswith("origin/") else branch
        except (OSError, subprocess.CalledProcessError):
            branch = _git_output(root, "rev-parse", "--abbrev-ref", "HEAD")
        relative = skill_path.relative_to(root).as_posix()
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None
    owner, repository = match.groups()
    return (
        f"https://github.com/{owner}/{repository}/blob/"
        f"{quote(branch, safe='')}/{quote(relative, safe='/')}"
    )


def build_inventory(registry_path, codex_home, plugin_skills=()):
    data, _ = load_registry(registry_path)
    entries = {
        os.path.normcase(str(Path(entry["local_path"]).resolve())): entry
        for entry in data["skills"]
        if entry.get("local_path")
    }
    type_order = {"personal": 0, "system": 1, "plugin": 2}
    rows = []
    for local_path in discover_skill_files(codex_home, plugin_skills):
        kind = skill_type(local_path, codex_home, plugin_skills)
        entry = entries.get(os.path.normcase(str(local_path)))
        github_url = (
            entry.get("upstream_url") if entry else infer_github_blob_url(local_path)
        )
        current_version = display_version(
            local_path,
            accepted_commit=entry.get("base_commit_sha") if entry else None,
            accepted_hash=entry.get("base_hash") if entry else None,
        )
        latest_version = entry.get("latest_version") if entry else None

        if entry is None:
            eligibility = (
                "managed_by_codex" if kind in {"system", "plugin"} else "cannot_check"
            )
        elif entry.get("status") == "check_failed":
            eligibility = "check_failed"
        elif entry.get("status") in {"review_required", "conflict"}:
            eligibility = "review_required"
        elif entry.get("status") == "update_available":
            eligibility = (
                "yes" if fast_eligibility(registry_path, entry["name"])["eligible"]
                else "review_required"
            )
        elif entry.get("status") == "no_update":
            eligibility = (
                "no"
                if sha256_file(local_path) == entry.get("base_hash")
                else "review_required"
            )
        else:
            eligibility = "cannot_check"

        rows.append(
            {
                "name": skill_name(local_path),
                "type": kind,
                "github_url": github_url,
                "current_version": current_version,
                "latest_version": latest_version,
                "update_eligibility": eligibility,
                "local_path": str(local_path),
            }
        )
    return sorted(
        rows,
        key=lambda row: (
            type_order[row["type"]],
            row["name"].casefold(),
            os.path.normcase(row["local_path"]),
        ),
    )


def migrate_registry(registry_path):
    registry_path = Path(registry_path)
    data, changed = load_registry(registry_path)
    backup_path = None
    if registry_path.exists() and changed:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_path = registry_path.with_name(f"{registry_path.name}.{stamp}.bak")
        atomic_copy(registry_path, backup_path)
    save_registry(registry_path, data)
    return backup_path


def build_pinned_raw_url(blob_url, upstream_ref, commit_sha):
    parsed = urlsplit(blob_url)
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        raise ValueError("upstream URL must use https://github.com")
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit_sha):
        raise ValueError("commit_sha must be a 40-character hexadecimal SHA")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 5 or parts[2] != "blob":
        raise ValueError("upstream URL must be a GitHub blob URL")
    ref_parts = [part for part in upstream_ref.split("/") if part]
    if parts[3 : 3 + len(ref_parts)] != ref_parts:
        raise ValueError("upstream_ref does not match the GitHub blob URL")
    file_parts = parts[3 + len(ref_parts) :]
    if not file_parts:
        raise ValueError("GitHub blob URL does not contain a file path")
    if file_parts[-1] != "SKILL.md":
        raise ValueError("GitHub blob URL must point to SKILL.md")
    owner, repo = parts[0], parts[1]
    encoded_path = "/".join(quote(part, safe="") for part in file_parts)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{commit_sha.lower()}/{encoded_path}"


def _build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    hash_parser = commands.add_parser("hash")
    hash_parser.add_argument("file")

    version_parser = commands.add_parser("extract-version")
    version_parser.add_argument("file")

    for command in ("validate", "migrate"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("--registry", required=True)

    list_parser = commands.add_parser("list")
    list_parser.add_argument("--registry", required=True)

    inventory = commands.add_parser("inventory")
    inventory.add_argument("--registry", required=True)
    inventory.add_argument("--codex-home", required=True)
    inventory.add_argument("--plugin-skill", action="append", default=[])

    fast_check = commands.add_parser("fast-eligibility")
    fast_check.add_argument("--registry", required=True)
    fast_check.add_argument("--name", required=True)

    fast_update = commands.add_parser("fast-apply")
    fast_update.add_argument("--registry", required=True)
    fast_update.add_argument("--name", required=True)
    fast_update.add_argument("--candidate-hash", required=True)

    stage = commands.add_parser("stage")
    stage.add_argument("--registry", required=True)
    stage.add_argument("--name", required=True)
    stage.add_argument("--local", required=True)
    stage.add_argument("--upstream-url", required=True)
    stage.add_argument("--ref", required=True)
    stage.add_argument("--candidate", required=True)
    stage.add_argument("--commit-sha", required=True)
    stage.add_argument("--version")

    conflict_add = commands.add_parser("conflict-add")
    conflict_add.add_argument("--registry", required=True)
    conflict_add.add_argument("--name", required=True)
    conflict_add.add_argument("--id", required=True)
    conflict_add.add_argument("--section", required=True)
    conflict_add.add_argument("--base-hash")
    conflict_add.add_argument("--local-hash", required=True)
    conflict_add.add_argument("--candidate-hash", required=True)

    conflict_resolve = commands.add_parser("conflict-resolve")
    conflict_resolve.add_argument("--registry", required=True)
    conflict_resolve.add_argument("--name", required=True)
    conflict_resolve.add_argument("--id", required=True)
    conflict_resolve.add_argument("--resolution", required=True)

    approve = commands.add_parser("approve")
    approve.add_argument("--registry", required=True)
    approve.add_argument("--name", required=True)
    approve.add_argument("--candidate-hash", required=True)
    approve.add_argument("--local-hash", required=True)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--registry", required=True)
    finalize.add_argument("--name", required=True)
    finalize.add_argument("--candidate-hash", required=True)

    backup = commands.add_parser("backup")
    backup.add_argument("--registry", required=True)
    backup.add_argument("--name", required=True)

    restore = commands.add_parser("restore")
    restore.add_argument("--registry", required=True)
    restore.add_argument("--name", required=True)
    restore.add_argument("--backup", required=True)

    failure = commands.add_parser("mark-failure")
    failure.add_argument("--registry", required=True)
    failure.add_argument("--name", required=True)
    failure.add_argument("--error", required=True)

    raw_url = commands.add_parser("raw-url")
    raw_url.add_argument("--url", required=True)
    raw_url.add_argument("--ref", required=True)
    raw_url.add_argument("--commit-sha", required=True)
    return parser


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "hash":
            print(sha256_file(args.file))
        elif args.command == "extract-version":
            print(extract_skill_version(args.file) or "")
        elif args.command == "validate":
            data, changed = load_registry(args.registry)
            validate_registry(data)
            print(json.dumps({"valid": True, "migration_required": changed}))
        elif args.command == "migrate":
            backup = migrate_registry(args.registry)
            print(json.dumps({"schema_version": SCHEMA_VERSION, "backup": str(backup) if backup else None}))
        elif args.command == "list":
            print(json.dumps(list_entries(args.registry), ensure_ascii=False, indent=2))
        elif args.command == "inventory":
            rows = build_inventory(args.registry, args.codex_home, args.plugin_skill)
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        elif args.command == "fast-eligibility":
            result = fast_eligibility(args.registry, args.name)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "fast-apply":
            result = fast_apply(
                args.registry, args.name, args.candidate_hash
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "stage":
            entry = stage_candidate(
                args.registry,
                args.name,
                args.local,
                args.upstream_url,
                args.ref,
                args.candidate,
                args.commit_sha,
                args.version,
            )
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        elif args.command == "conflict-add":
            entry = add_conflict(
                args.registry,
                args.name,
                args.id,
                args.section,
                args.base_hash,
                args.local_hash,
                args.candidate_hash,
            )
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        elif args.command == "conflict-resolve":
            entry = resolve_conflict(args.registry, args.name, args.id, args.resolution)
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        elif args.command == "approve":
            entry = approve_candidate(
                args.registry, args.name, args.candidate_hash, args.local_hash
            )
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        elif args.command == "finalize":
            entry = finalize_candidate(args.registry, args.name, args.candidate_hash)
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        elif args.command == "backup":
            print(create_backup(args.registry, args.name))
        elif args.command == "restore":
            print(restore_backup(args.registry, args.name, args.backup))
        elif args.command == "mark-failure":
            entry = mark_failure(args.registry, args.name, args.error)
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        elif args.command == "raw-url":
            print(build_pinned_raw_url(args.url, args.ref, args.commit_sha))
        return 0
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
