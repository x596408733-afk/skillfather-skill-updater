#!/usr/bin/env python3
"""Deterministic state operations for skillfather-skill-updater."""

import argparse
import hashlib
import json
import os
import re
import shutil
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


def empty_registry():
    return {"schema_version": SCHEMA_VERSION, "skills": []}


def _migrate_entry(source, force_first_review=False):
    entry = dict(source)
    entry["status"] = STATUS_MAP.get(entry.get("status"), entry.get("status", "unregistered"))
    entry["base_hash"] = normalize_hash(entry.get("base_hash"))
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
    candidate_snapshot = _snapshot_path(registry_path, name, "candidate", candidate_hash)
    atomic_copy(candidate_path, candidate_snapshot)

    if existing is None:
        entry = {"name": name, "pending_conflicts": []}
        data["skills"].append(entry)
        if local_hash == candidate_hash:
            base_snapshot = _snapshot_path(registry_path, name, "base", candidate_hash)
            atomic_copy(candidate_path, base_snapshot)
            entry["base_snapshot"] = _relative_to_registry(registry_path, base_snapshot)
            entry["base_hash"] = candidate_hash
            entry["first_diff_required"] = False
            status = "no_update"
        else:
            entry["base_snapshot"] = None
            entry["base_hash"] = None
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
            "latest_version": latest_version,
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
    base_snapshot = _snapshot_path(registry_path, name, "base", candidate_hash)
    atomic_copy(candidate_snapshot, base_snapshot)
    entry["base_snapshot"] = _relative_to_registry(registry_path, base_snapshot)
    entry["base_hash"] = candidate_hash
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

    for command in ("validate", "migrate"):
        command_parser = commands.add_parser(command)
        command_parser.add_argument("--registry", required=True)

    list_parser = commands.add_parser("list")
    list_parser.add_argument("--registry", required=True)

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
        elif args.command == "validate":
            data, changed = load_registry(args.registry)
            validate_registry(data)
            print(json.dumps({"valid": True, "migration_required": changed}))
        elif args.command == "migrate":
            backup = migrate_registry(args.registry)
            print(json.dumps({"schema_version": SCHEMA_VERSION, "backup": str(backup) if backup else None}))
        elif args.command == "list":
            print(json.dumps(list_entries(args.registry), ensure_ascii=False, indent=2))
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
