# Skill Updater Dashboard And Fast Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a complete six-column Codex Skill inventory and a one-command fast update path that automatically falls back to guarded review when local customization exists.

**Architecture:** Keep `scripts/skill_update_state.py` as the deterministic owner of parsing, hashing, inventory classification, backup, fast eligibility, atomic writes, and registry mutation. Keep GitHub retrieval and bounded concurrency agent-owned and document the exact orchestration in `SKILL.md` and `references/protocol.md`. Reuse registry v2 and derive display metadata from actual local and pinned candidate bytes instead of trusting stale stored versions.

**Tech Stack:** Python 3.8+ standard library, `unittest`, Git, Markdown Skill protocol.

---

## File Map

- Modify `scripts/skill_update_state.py`: version extraction, inventory discovery/classification, fast eligibility/apply commands, and finalization metadata refresh.
- Modify `tests/test_skill_update_state.py`: deterministic unit coverage for versions, inventory, eligibility, backup, fast apply, and failure guards.
- Modify `tests/test_skill_package.py`: command and documentation contract tests.
- Modify `SKILL.md`: bare dashboard workflow, fast command, fallback rules, bounded repository checks, and final inventory contract.
- Modify `references/protocol.md`: exact inventory schema, version rules, retrieval fallback, concurrency, and fast update protocol.
- Modify `README.md`: public command examples, table fields, and safety behavior.
- Modify `agents/openai.yaml`: mention dashboard and fast update in the default prompt without expanding the trigger description.
- Create `docs/superpowers/skill-tests/2026-07-11-dashboard-fast-mode-behavior.md`: baseline and post-change agent behavior evidence.
- Use existing `docs/superpowers/specs/2026-07-11-skill-updater-dashboard-fast-mode-design.md` as the accepted design reference.

### Task 1: Derive Versions From Actual Skill Bytes

**Files:**
- Modify: `tests/test_skill_update_state.py`
- Modify: `scripts/skill_update_state.py`

- [ ] **Step 1: Write failing version tests**

Add these tests to `SkillUpdateStateTests`:

```python
def test_extract_skill_version_reads_top_level_frontmatter(self):
    self.local.write_text(
        '---\nname: demo-skill\nversion: "1.8.9"\n---\nbody\n',
        encoding="utf-8",
    )
    self.assertEqual("1.8.9", state.extract_skill_version(self.local))

def test_extract_skill_version_ignores_body_version_text(self):
    self.local.write_text(
        "---\nname: demo-skill\n---\nversion: fake\n",
        encoding="utf-8",
    )
    self.assertIsNone(state.extract_skill_version(self.local))

def test_display_version_falls_back_to_hash(self):
    self.local.write_text("no frontmatter version\n", encoding="utf-8")
    expected = state.sha256_file(self.local).split(":", 1)[1][:12]
    self.assertEqual(expected, state.display_version(self.local))

def test_finalize_refreshes_stale_local_version_from_final_file(self):
    self.local.write_text(
        "---\nname: demo-skill\nversion: 1.0\n---\n",
        encoding="utf-8",
    )
    self.candidate.write_text(
        "---\nname: demo-skill\nversion: 2.0\n---\n",
        encoding="utf-8",
    )
    entry = self.stage()
    self.local.write_bytes(self.candidate.read_bytes())
    local_hash = state.sha256_file(self.local)
    state.approve_candidate(self.registry, "demo-skill", entry["candidate_hash"], local_hash)
    finalized = state.finalize_candidate(
        self.registry, "demo-skill", entry["candidate_hash"]
    )
    self.assertEqual("2.0", finalized["local_version"])
    self.assertEqual("2.0", finalized["latest_version"])
```

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```powershell
python -X utf8 -m unittest `
  tests.test_skill_update_state.SkillUpdateStateTests.test_extract_skill_version_reads_top_level_frontmatter `
  tests.test_skill_update_state.SkillUpdateStateTests.test_extract_skill_version_ignores_body_version_text `
  tests.test_skill_update_state.SkillUpdateStateTests.test_display_version_falls_back_to_hash `
  tests.test_skill_update_state.SkillUpdateStateTests.test_finalize_refreshes_stale_local_version_from_final_file -v
```

Expected: failures because `extract_skill_version` and `display_version` do not exist and finalization leaves stale metadata.

- [ ] **Step 3: Implement minimal frontmatter version parsing**

Add near the hash helpers in `scripts/skill_update_state.py`:

```python
def extract_skill_version(path):
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        match = re.fullmatch(r"version:\s*(.*)", line)
        if not match:
            continue
        scalar = match.group(1)
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
```

In `stage_candidate`, derive candidate display metadata from the pinned candidate:

```python
candidate_version = (
    latest_version
    if latest_version is not None and latest_version.strip()
    else extract_skill_version(candidate_path) or commit_sha.lower()[:12]
)
```

Blank explicit versions are treated as absent. The minimal parser preserves `#` unless
preceded by whitespace and deliberately rejects quoted scalars containing escaped quote
syntax rather than partially parsing them.

Store `candidate_version` as `latest_version`. For a new entry, initialize `local_version` with `display_version(local_path)`. In `finalize_candidate`, after rechecking the approved local hash, set:

```python
entry["local_version"] = display_version(
    entry["local_path"],
    accepted_commit=entry.get("candidate_commit_sha"),
    accepted_hash=entry.get("candidate_hash"),
)
entry["latest_version"] = (
    extract_skill_version(candidate_snapshot)
    or entry["candidate_commit_sha"][:12]
)
```

- [ ] **Step 4: Run focused and full tests**

Run:

```powershell
python -X utf8 -m unittest tests.test_skill_update_state -v
python -X utf8 -m unittest discover -s tests -v
```

Expected: all tests pass with no skipped tests.

- [ ] **Step 5: Commit version truth changes**

```powershell
git add scripts/skill_update_state.py tests/test_skill_update_state.py
git commit -m "fix: derive Skill versions from files"
```

### Task 2: Add Deterministic Fast Eligibility And Apply

**Files:**
- Modify: `tests/test_skill_update_state.py`
- Modify: `scripts/skill_update_state.py`

- [ ] **Step 1: Write failing fast-path tests**

Add tests that first register identical v1 bytes as base, then stage v2 as the candidate:

```python
def prepare_fast_update(self):
    self.local.write_text("---\nname: demo-skill\nversion: 1\n---\n", encoding="utf-8")
    self.candidate.write_bytes(self.local.read_bytes())
    self.stage()
    self.candidate.write_text("---\nname: demo-skill\nversion: 2\n---\n", encoding="utf-8")
    return self.stage()

def test_fast_eligibility_accepts_unchanged_local_base(self):
    entry = self.prepare_fast_update()
    result = state.fast_eligibility(self.registry, "demo-skill")
    self.assertTrue(result["eligible"])
    self.assertEqual(entry["candidate_hash"], result["candidate_hash"])

def test_fast_eligibility_rejects_one_byte_local_customization(self):
    self.prepare_fast_update()
    self.local.write_text("customized\n", encoding="utf-8")
    result = state.fast_eligibility(self.registry, "demo-skill")
    self.assertFalse(result["eligible"])
    self.assertEqual("local_differs_from_base", result["reason"])

def test_fast_apply_backs_up_and_finalizes_pinned_candidate(self):
    entry = self.prepare_fast_update()
    result = state.fast_apply(self.registry, "demo-skill", entry["candidate_hash"])
    self.assertEqual(self.candidate.read_bytes(), self.local.read_bytes())
    self.assertTrue(Path(result["backup_path"]).is_file())
    self.assertEqual("no_update", result["entry"]["status"])
    self.assertEqual("2", result["entry"]["local_version"])

def test_fast_apply_rejects_mutated_candidate_snapshot(self):
    entry = self.prepare_fast_update()
    snapshot = self.registry.parent / entry["candidate_snapshot"]
    snapshot.write_text("tampered\n", encoding="utf-8")
    with self.assertRaisesRegex(ValueError, "candidate snapshot hash mismatch"):
        state.fast_apply(self.registry, "demo-skill", entry["candidate_hash"])

def test_fast_eligibility_rejects_first_review(self):
    self.local.write_text("local\n", encoding="utf-8")
    self.candidate.write_text("upstream\n", encoding="utf-8")
    self.stage()
    result = state.fast_eligibility(self.registry, "demo-skill")
    self.assertFalse(result["eligible"])
    self.assertEqual("first_review_required", result["reason"])

def test_fast_eligibility_rejects_unresolved_conflicts(self):
    entry = self.prepare_fast_update()
    state.add_conflict(
        self.registry,
        "demo-skill",
        "demo-skill#body#1",
        "body",
        entry["base_hash"],
        entry["local_hash_at_check"],
        entry["candidate_hash"],
    )
    result = state.fast_eligibility(self.registry, "demo-skill")
    self.assertFalse(result["eligible"])
    self.assertEqual("unresolved_conflicts", result["reason"])

def test_fast_apply_rejects_stale_requested_candidate_hash(self):
    self.prepare_fast_update()
    with self.assertRaisesRegex(ValueError, "candidate hash changed"):
        state.fast_apply(self.registry, "demo-skill", "sha256:" + "f" * 64)

def test_fast_apply_rechecks_local_hash_immediately_before_write(self):
    entry = self.prepare_fast_update()
    eligible = state.fast_eligibility(self.registry, "demo-skill")
    self.local.write_text("changed after eligibility\n", encoding="utf-8")
    with mock.patch.object(state, "fast_eligibility", return_value=eligible):
        with self.assertRaisesRegex(ValueError, "local file changed"):
            state.fast_apply(self.registry, "demo-skill", entry["candidate_hash"])
```

Add `from unittest import mock` to the test imports for the last race-window test.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
python -X utf8 -m unittest tests.test_skill_update_state.SkillUpdateStateTests.test_fast_eligibility_accepts_unchanged_local_base tests.test_skill_update_state.SkillUpdateStateTests.test_fast_apply_backs_up_and_finalizes_pinned_candidate -v
```

Expected: failures because the fast functions do not exist.

- [ ] **Step 3: Implement read-only eligibility**

Add:

```python
def fast_eligibility(registry_path, name):
    data, _ = load_registry(registry_path)
    entry = _find_entry(data, name)
    checks = (
        (entry.get("first_diff_required", True), "first_review_required"),
        (any(item.get("status", "unresolved") == "unresolved"
             for item in entry.get("pending_conflicts", [])), "unresolved_conflicts"),
        (not entry.get("base_hash"), "missing_base"),
        (not entry.get("candidate_snapshot"), "missing_candidate"),
        (sha256_file(entry["local_path"]) != entry.get("base_hash"),
         "local_differs_from_base"),
        (entry.get("candidate_hash") == entry.get("base_hash"), "no_update"),
    )
    for failed, reason in checks:
        if failed:
            return {"eligible": False, "reason": reason, "name": name}
    return {
        "eligible": True,
        "reason": "eligible",
        "name": name,
        "candidate_hash": entry["candidate_hash"],
        "local_hash": sha256_file(entry["local_path"]),
    }
```

Return machine reasons only; localization remains presentation-owned.

- [ ] **Step 4: Implement atomic fast apply and CLI commands**

Implement `fast_apply(registry_path, name, candidate_hash)` in this order:

1. Call `fast_eligibility` and reject an ineligible result with its reason.
2. Require the requested candidate hash to equal the registered candidate hash.
3. Recheck candidate snapshot SHA-256.
4. Create the managed backup with `create_backup`.
5. Recheck the local hash against the eligibility result immediately before writing.
6. `atomic_copy` the candidate snapshot to local.
7. Call `approve_candidate` with the new local hash.
8. Call `finalize_candidate`.
9. Return `{"backup_path": str(backup), "entry": finalized}`.

Add CLI parsers and JSON output:

```text
fast-eligibility --registry REGISTRY --name NAME
fast-apply --registry REGISTRY --name NAME --candidate-hash SHA256
```

- [ ] **Step 5: Run fast-path and full tests**

Run:

```powershell
python -X utf8 -m unittest tests.test_skill_update_state -v
python -X utf8 -m unittest discover -s tests -v
```

Expected: every fast-path safety test and all legacy tests pass.

- [ ] **Step 6: Commit fast path**

```powershell
git add scripts/skill_update_state.py tests/test_skill_update_state.py
git commit -m "feat: add guarded fast Skill updates"
```

### Task 3: Build The Complete Inventory

**Files:**
- Modify: `tests/test_skill_update_state.py`
- Modify: `scripts/skill_update_state.py`

- [ ] **Step 1: Write failing discovery and classification tests**

Create temporary personal, system, and plugin Skill paths and assert exact-path registry matching:

```python
def test_inventory_reports_required_columns_and_exact_path_identity(self):
    codex_home = self.root / ".codex"
    personal = codex_home / "skills" / "demo-skill" / "SKILL.md"
    system = codex_home / "skills" / ".system" / "builtin" / "SKILL.md"
    plugin = codex_home / "plugins" / "cache" / "demo" / "1" / "skills" / "tool" / "SKILL.md"
    for path, name, version in (
        (personal, "demo-skill", "1.0"),
        (system, "builtin", "2.0"),
        (plugin, "tool", "3.0"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nname: {name}\nversion: {version}\n---\n",
            encoding="utf-8",
        )
    self.local = personal
    self.candidate.write_bytes(personal.read_bytes())
    self.stage()

    rows = state.build_inventory(self.registry, codex_home, plugin_skills=[plugin])

    by_name = {row["name"]: row for row in rows}
    self.assertEqual(
        {"name", "type", "github_url", "current_version",
         "latest_version", "update_eligibility", "local_path"},
        set(by_name["demo-skill"]),
    )
    self.assertEqual("personal", by_name["demo-skill"]["type"])
    self.assertEqual("system", by_name["builtin"]["type"])
    self.assertEqual("plugin", by_name["tool"]["type"])
    self.assertEqual("managed_by_codex", by_name["builtin"]["update_eligibility"])

def test_inventory_uses_file_version_and_update_state(self):
    codex_home = self.root / ".codex"
    self.local = codex_home / "skills" / "demo-skill" / "SKILL.md"
    self.local.parent.mkdir(parents=True)
    self.local.write_text(
        "---\nname: demo-skill\nversion: 1.0\n---\n",
        encoding="utf-8",
    )
    self.candidate.write_bytes(self.local.read_bytes())
    self.stage()
    registry = self.read_registry()
    registry["skills"][0]["local_version"] = "stale"
    self.registry.write_text(json.dumps(registry), encoding="utf-8")
    row = state.build_inventory(self.registry, codex_home)[0]
    self.assertEqual("1.0", row["current_version"])
    self.assertEqual("no", row["update_eligibility"])

    self.candidate.write_text(
        "---\nname: demo-skill\nversion: 2.0\n---\n",
        encoding="utf-8",
    )
    self.stage()
    row = state.build_inventory(self.registry, codex_home)[0]
    self.assertEqual("2.0", row["latest_version"])
    self.assertEqual("yes", row["update_eligibility"])

    self.local.write_text("local customization\n", encoding="utf-8")
    row = state.build_inventory(self.registry, codex_home)[0]
    self.assertEqual("review_required", row["update_eligibility"])

def test_inventory_maps_failure_unregistered_and_managed_states(self):
    codex_home = self.root / ".codex"
    registered = codex_home / "skills" / "registered" / "SKILL.md"
    personal = codex_home / "skills" / "unregistered" / "SKILL.md"
    system = codex_home / "skills" / ".system" / "builtin" / "SKILL.md"
    plugin = codex_home / "plugins" / "cache" / "p" / "1" / "skills" / "tool" / "SKILL.md"
    for path, name in (
        (registered, "registered"),
        (personal, "unregistered"),
        (system, "builtin"),
        (plugin, "tool"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
    self.local = registered
    self.candidate.write_bytes(registered.read_bytes())
    state.stage_candidate(
        registry_path=self.registry,
        name="registered",
        local_path=registered,
        upstream_url="https://github.com/example/registered/blob/main/SKILL.md",
        upstream_ref="main",
        candidate_path=self.candidate,
        commit_sha="a" * 40,
        latest_version="1",
    )
    state.mark_failure(self.registry, "registered", "network unavailable")
    rows = {row["name"]: row for row in state.build_inventory(self.registry, codex_home)}
    self.assertEqual("check_failed", rows["registered"]["update_eligibility"])
    self.assertEqual("cannot_check", rows["unregistered"]["update_eligibility"])
    self.assertEqual("managed_by_codex", rows["builtin"]["update_eligibility"])
    self.assertEqual("managed_by_codex", rows["tool"]["update_eligibility"])

def test_inventory_same_name_different_path_does_not_inherit_url(self):
    codex_home = self.root / ".codex"
    registered = codex_home / "skills" / "registered" / "SKILL.md"
    duplicate = codex_home / "skills" / "duplicate" / "SKILL.md"
    for path in (registered, duplicate):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("---\nname: demo-skill\n---\n", encoding="utf-8")
    self.local = registered
    self.candidate.write_bytes(registered.read_bytes())
    self.stage()
    rows = [row for row in state.build_inventory(self.registry, codex_home)
            if row["name"] == "demo-skill"]
    self.assertEqual(2, len(rows))
    by_path = {row["local_path"]: row for row in rows}
    self.assertIsNotNone(by_path[str(registered.resolve())]["github_url"])
    self.assertIsNone(by_path[str(duplicate.resolve())]["github_url"])

def test_discover_skill_files_deduplicates_resolved_paths(self):
    codex_home = self.root / ".codex"
    path = codex_home / "skills" / "demo" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    discovered = state.discover_skill_files(codex_home)
    self.assertEqual([path.resolve()], discovered)

def test_infer_github_blob_url_requires_verified_https_remote(self):
    skill = self.root / "repo" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("---\nname: demo\n---\n", encoding="utf-8")
    outputs = [
        str(self.root / "repo"),
        "https://github.com/example/repository.git",
        "origin/main",
    ]
    with mock.patch.object(state, "_git_output", side_effect=outputs):
        result = state.infer_github_blob_url(skill)
    self.assertEqual(
        "https://github.com/example/repository/blob/main/skills/demo/SKILL.md",
        result,
    )

def test_infer_github_blob_url_rejects_unverified_remotes(self):
    skill = self.root / "repo" / "SKILL.md"
    skill.parent.mkdir()
    skill.write_text("---\nname: demo\n---\n", encoding="utf-8")
    for remote in (
        "git@github.com:example/repository.git",
        "https://example.com/example/repository.git",
        "not-a-url",
    ):
        with self.subTest(remote=remote):
            outputs = [str(self.root / "repo"), remote]
            with mock.patch.object(state, "_git_output", side_effect=outputs):
                self.assertIsNone(state.infer_github_blob_url(skill))
    with mock.patch.object(state, "_git_output", side_effect=OSError("git missing")):
        self.assertIsNone(state.infer_github_blob_url(skill))
```

- [ ] **Step 2: Run inventory tests and verify RED**

Run:

```powershell
python -X utf8 -m unittest tests.test_skill_update_state.SkillUpdateStateTests.test_inventory_reports_required_columns_and_exact_path_identity -v
```

Expected: failure because `build_inventory` does not exist.

- [ ] **Step 3: Implement Skill discovery and GitHub remote inference**

Add focused helpers:

```python
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


def skill_type(path, codex_home, plugin_skills=()):
    path = Path(path).resolve()
    codex_home = Path(codex_home).resolve()
    system_root = codex_home / "skills" / ".system"
    plugin_root = codex_home / "plugins" / "cache"
    normalized_plugins = {
        os.path.normcase(str(Path(item).resolve())) for item in plugin_skills
    }
    if os.path.normcase(str(path)) in normalized_plugins:
        return "plugin"
    try:
        path.relative_to(system_root)
        return "system"
    except ValueError:
        pass
    try:
        path.relative_to(plugin_root)
        return "plugin"
    except ValueError:
        return "personal"
```

Import `subprocess` and implement verified GitHub remote inference:

```python
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
        root = Path(_git_output(skill_path.parent, "rev-parse", "--show-toplevel")).resolve()
        remote = _git_output(root, "config", "--get", "remote.origin.url")
        match = re.fullmatch(
            r"https://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?",
            remote,
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
```

Tests must mock `_git_output`; they must not depend on the machine's Git repositories.

- [ ] **Step 4: Implement inventory rows and CLI**

Implement `build_inventory(registry_path, codex_home, plugin_skills=())` so each row contains exactly:

```python
{
    "name": name,
    "type": kind,
    "github_url": registered_url_or_verified_remote_or_none,
    "current_version": display_version(local_path, accepted_commit, base_hash),
    "latest_version": registered_latest_or_none,
    "update_eligibility": machine_label,
    "local_path": str(local_path),
}
```

Match entries only by resolved absolute `local_path`. Sort by type order `personal`, `system`, `plugin`, then case-folded name and path. Add:

```text
inventory --registry REGISTRY --codex-home CODEX_HOME [--plugin-skill SKILL.md ...]
```

The CLI accepts repeated `--plugin-skill` paths from the current Codex Skill catalog and emits UTF-8 JSON. When none are supplied, it falls back to the plugin cache scan. Presentation renders `None` as `无` or `未知` in the user's language.

- [ ] **Step 5: Run focused and full tests**

Run:

```powershell
python -X utf8 -m unittest tests.test_skill_update_state -v
python -X utf8 -m unittest discover -s tests -v
```

Expected: all inventory and existing state tests pass.

- [ ] **Step 6: Commit inventory**

```powershell
git add scripts/skill_update_state.py tests/test_skill_update_state.py
git commit -m "feat: add complete Skill inventory"
```

### Task 4: Teach The Agent Dashboard, Batching, And Fallbacks

**Files:**
- Modify: `tests/test_skill_package.py`
- Modify: `SKILL.md`
- Modify: `references/protocol.md`
- Modify: `README.md`
- Modify: `agents/openai.yaml`
- Create: `docs/superpowers/skill-tests/2026-07-11-dashboard-fast-mode-behavior.md`

- [ ] **Step 1: Run and record RED behavior scenarios**

Run each prompt with a fresh worker against disposable fixture paths and the current pre-change Skill. Do not let workers access or mutate the real registry.

Scenario A, complete inventory:

```text
The disposable Codex home contains one personal registered Skill, one unregistered
personal Skill, one system Skill, and one plugin Skill. Handle bare `/skill-update`.
Show the final result exactly as this Skill requires. Do not invent GitHub URLs.
```

Failure criteria: missing any Skill, missing any requested column, trusting stale registry versions, or presenting an unverified URL.

Scenario B, fast-path downgrade:

```text
Two registered Skills have staged updates. Skill A local hash equals its accepted
base. Skill B differs from its accepted base by one byte. Handle
`/skill-update fast all` and state every write, backup, and confirmation required.
```

Failure criteria: no fast route for A, automatic replacement of B, no backup/hash recheck, or treating the `fast` command as approval for the guarded B merge.

Scenario C, grouped refresh under failure:

```text
Six Skills map to three GitHub repositories; three Skills share repository R1.
Repository R2 raw download fails but its Contents API succeeds. Repository R3
fails every retrieval method. Handle `/skill-update check all` and explain the
number of ref resolutions, concurrency bound, fallback order, and final rows.
```

Failure criteria: resolving R1 more than once, exceeding four concurrent repository groups, stopping all checks after R3 fails, changing R3's last valid base/candidate, or omitting failed rows.

Create `docs/superpowers/skill-tests/2026-07-11-dashboard-fast-mode-behavior.md` with each prompt, the worker's response, and exact failure observations. RED is established only when at least one defined failure occurs in each scenario.

- [ ] **Step 2: Write failing package contract tests**

Extend `test_skill_metadata_and_commands_are_consistent` and replace the old three-string inventory assertion with explicit contracts:

```python
for command in ("inventory", "fast"):
    self.assertIn(f"/skill-update {command}", skill)

for document in (skill, protocol, readme):
    for column in (
        "GitHub address",
        "Current version",
        "Latest version",
        "Update eligibility",
    ):
        self.assertIn(column, document)
    self.assertIn("Review required", document)
    self.assertIn("Managed by Codex", document)

self.assertIn("at most four", protocol)
self.assertIn("GitHub Contents API", protocol)
self.assertIn("raw.githubusercontent.com", protocol)
self.assertIn("shallow Git", protocol)
self.assertIn("local hash equals", protocol)
self.assertIn("automatically", protocol)
```

- [ ] **Step 3: Run package tests and verify RED**

Run:

```powershell
python -X utf8 -m unittest tests.test_skill_package -v
```

Expected: failures for missing fast command, table columns, concurrency, and fallback rules.

- [ ] **Step 4: Update `SKILL.md`**

Add commands:

```text
/skill-update
/skill-update inventory
/skill-update fast <name|selection|all>
```

Replace the unconditional full-file prohibition with a scoped rule:

```text
Never replace a complete customized local file. Exact candidate replacement is allowed
only through `fast-apply` after every fast-eligibility guard, backup, and last-moment
hash check succeeds.
```

Define bare-command behavior: immediate local inventory, bounded refresh, final six-column inventory, then selection. Define explicit `fast` as approval only for the eligible set; every ineligible row automatically enters guarded planning.

- [ ] **Step 5: Update protocol and README**

Document:

- exact table schema and localized machine labels;
- current-catalog `--plugin-skill` inputs, with plugin-cache fallback only when unavailable;
- version resolution and stale-version correction;
- repository/ref grouping and at most four repository groups concurrently;
- reuse when resolved SHA and valid candidate are unchanged;
- Contents API, Raw, shallow Git fallback order pinned to one commit;
- per-repository failure isolation;
- fast eligibility, backup, atomic copy, approval, finalization, and downgrade;
- verified local Git remote inference and `无` when proof is absent;
- command examples for inventory, fast one, and fast all.

Update `agents/openai.yaml` default prompt to request the dashboard before updates and use fast mode only when eligible.

- [ ] **Step 6: Re-run behavior scenarios for GREEN**

Run the same three prompts with fresh workers that load the modified Skill. Append their responses to the behavior evidence file. Every listed failure criterion must now be absent; record the relevant response lines proving inventory completeness, fast downgrade, backup/hash checks, grouping, concurrency, fallback, and failure isolation.

- [ ] **Step 7: Run package and full tests**

Run:

```powershell
python -X utf8 -m unittest tests.test_skill_package -v
python -X utf8 -m unittest discover -s tests -v
git diff --check
```

Expected: all tests pass, no skipped tests, and no whitespace errors.

- [ ] **Step 8: Commit protocol and public docs**

```powershell
git add SKILL.md references/protocol.md README.md agents/openai.yaml tests/test_skill_package.py docs/superpowers/skill-tests/2026-07-11-dashboard-fast-mode-behavior.md
git commit -m "docs: add fast dashboard workflow"
```

### Task 5: Verify Against A Realistic Registry Copy

**Files:**
- Test only; do not commit user registry data.

- [ ] **Step 1: Run full package verification**

```powershell
python -X utf8 -m unittest discover -s tests -v
python -X utf8 C:\Users\59640\.codex\skills\.system\skill-creator\scripts\quick_validate.py .
git diff --check
```

Expected: all unit tests pass. If `quick_validate.py` is absent, report it as unavailable rather than claiming it passed.

- [ ] **Step 2: Test inventory on a disposable registry copy**

Copy the real registry to a fixed disposable path, then run:

```powershell
$tempRegistry = Join-Path $env:TEMP 'skill-update-registry-inventory-test.json'
Copy-Item -LiteralPath 'C:\Users\59640\.codex\skill-update-registry.json' -Destination $tempRegistry -Force
python -X utf8 scripts\skill_update_state.py inventory `
  --registry $tempRegistry `
  --codex-home C:\Users\59640\.codex
Remove-Item -LiteralPath $tempRegistry -Force
```

Expected: valid JSON; every row has all seven machine fields; registered URLs match by exact path; personal, system, and plugin rows appear; the real registry is unchanged.

- [ ] **Step 3: Exercise fast eligibility without writing**

Run `fast-eligibility` against the disposable registry for:

- one `no_update` Skill, expecting `no_update`;
- one customized or first-review Skill, expecting a guarded reason;
- one staged update in a temporary fixture, expecting `eligible`.

Do not run `fast-apply` against the user's installed Skills during this verification task.

- [ ] **Step 4: Review the complete diff**

```powershell
git status --short
git diff main...HEAD --stat
git diff main...HEAD -- SKILL.md references/protocol.md scripts/skill_update_state.py tests
```

Expected: only planned Skill package, tests, docs, and design/plan files changed.

- [ ] **Step 5: Commit verification-only corrections if needed**

If verification exposes a defect, reproduce it with a failing test, implement the minimal correction, rerun all verification, and commit:

```powershell
git add scripts/skill_update_state.py tests/test_skill_update_state.py tests/test_skill_package.py SKILL.md references/protocol.md README.md agents/openai.yaml
git commit -m "fix: correct fast update verification"
```

If no correction is needed, do not create an empty commit.

### Task 6: Review, Integrate, Install, And Publish

**Files:**
- Sync verified package files to `C:\Users\59640\.codex\skills\skillfather-skill-updater`
- Update Git branch and GitHub repository.

- [ ] **Step 1: Run code review**

Use `requesting-code-review` against `main...feature/skill-updater-dashboard-fast-mode`. Resolve every correctness or safety finding with a failing test before changing code.

- [ ] **Step 2: Run final verification after review**

```powershell
python -X utf8 -m unittest discover -s tests -v
git diff --check
git status --short
```

Expected: all tests pass, no skipped tests, no whitespace errors, and only intentional commits exist.

- [ ] **Step 3: Integrate the feature branch**

Use `finishing-a-development-branch` to merge the reviewed branch into `main` without rewriting unrelated history. Re-run the full suite on `main`.

- [ ] **Step 4: Sync the installed Skill**

After main verification, copy only the package-owned runtime files from the repository to:

```text
C:\Users\59640\.codex\skills\skillfather-skill-updater\SKILL.md
C:\Users\59640\.codex\skills\skillfather-skill-updater\agents\openai.yaml
C:\Users\59640\.codex\skills\skillfather-skill-updater\references\protocol.md
C:\Users\59640\.codex\skills\skillfather-skill-updater\scripts\skill_update_state.py
```

Compare SHA-256 for every copied file. Do not copy tests, Git metadata, design docs, or README into the installed runtime directory.

- [ ] **Step 5: Smoke-test the installed helper**

Run installed `validate`, `inventory`, and read-only `fast-eligibility` commands against the real registry. Confirm the six requested presentation fields can be rendered and that no Skill file changes during the smoke test.

- [ ] **Step 6: Push GitHub and verify remote state**

```powershell
git push origin main
git rev-parse HEAD
git rev-parse origin/main
```

Expected: both commit SHAs match. Fetch the remote `SKILL.md` pinned to that SHA and verify its SHA-256 equals the local committed file.

- [ ] **Step 7: Final report**

Report test totals, installed file hashes, GitHub commit, fast-path safety behavior, any unavailable verifier, and the complete six-column Skill inventory. Use `无` or `未知` explicitly where a verified address or version is unavailable.
