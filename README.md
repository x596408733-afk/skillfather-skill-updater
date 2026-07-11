<div align="center">

# 🧔 SkillFather

### Never let a Skill update destroy your local changes.

A safety-first updater for Codex and Claude Code Skills.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Codex](https://img.shields.io/badge/Codex-Skill-111111)](https://github.com/x596408733-afk/skillfather-skill-updater)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Compatible-D97757)](https://github.com/x596408733-afk/skillfather-skill-updater)

**Check updates. Compare changes. Protect local work.**

</div>

---

## ✨ What is SkillFather?

**SkillFather** helps you safely update locally installed `SKILL.md` files from GitHub.

Instead of directly overwriting your local Skill, it:

- 🔍 compares local and remote versions
- 📌 pins remote files to an exact Git commit
- #️⃣ verifies files with SHA-256
- 💾 creates backups before updates
- 🔀 protects locally customized content
- 🚧 stops when conflicts are detected
- ✅ requires confirmation before applying changes

It works with both **Codex** and **Claude Code**.

---

## 🚀 Main Features

- 📋 Scan locally installed Skills
- 🔗 Register verified GitHub sources
- 🏷️ Show current and latest versions
- ⚡ Fast-update unchanged Skills
- 🔀 Review customized Skills before merging
- 💾 Back up and restore local files
- 🛡️ Detect modified or unsafe candidates
- 🧭 Identify personal, system, and plugin Skills

Run:

```text
/skill-update
```

You will get a dashboard like this:

| Skill | Type | Current | Latest | Status |
| --- | --- | --- | --- | --- |
| example-skill | Personal | 1.2.0 | 1.3.0 | Yes |
| custom-skill | Personal | 2.0.0 | 2.1.0 | Review required |
| system-skill | System | 未知 | 未知 | Managed by Codex |

---

## 📦 Installation

### Codex on Windows

```powershell
git clone https://github.com/x596408733-afk/skillfather-skill-updater.git "$env:USERPROFILE\.codex\skills\skillfather-skill-updater"
```

### Codex on macOS or Linux

```bash
git clone https://github.com/x596408733-afk/skillfather-skill-updater.git ~/.codex/skills/skillfather-skill-updater
```

### Claude Code on macOS or Linux

```bash
git clone https://github.com/x596408733-afk/skillfather-skill-updater.git ~/.claude/skills/skillfather-skill-updater
```

Restart or reload the agent after installation.

Requirements:

- Python 3.8+
- Git

---

## ⚡ Quick Start

### Open the dashboard

```text
/skill-update
```

### Register a Skill

```text
/skill-update register <local-skill-path> <github-url>
```

### Check all updates

```text
/skill-update check all
```

### Update unchanged Skills

```text
/skill-update fast all
```

### Review changes

```text
/skill-update diff <name>
```

### Merge a customized Skill

```text
/skill-update merge <name>
```

### Restore a backup

```text
/skill-update restore <name> <backup-path>
```

---

## 🛡️ How Safety Works

```text
Local Skill
    ↓
Check accepted version
    ↓
Download pinned GitHub candidate
    ↓
Verify commit SHA and SHA-256
    ↓
Compare local, base, and remote files
    ↓
Backup
    ↓
User confirmation
    ↓
Apply update
```

The fast-update path is available only when the local file still matches its accepted base.

If SkillFather detects:

- local customization
- missing candidates
- changed file hashes
- conflicts
- unknown GitHub sources

it stops the fast update and switches to manual review.

---

## 🆕 Latest Improvements

The latest version improves dashboard accuracy and update integrity:

- Added `base_commit_sha` to track the version actually installed locally
- Separated the current local version from the newest GitHub candidate
- Detects local edits even after a previous `no_update` result
- Rejects missing or modified candidate snapshots
- Added an independent version extraction command
- Expanded automated tests from 51 to 55

Version extraction:

```bash
python -X utf8 scripts/skill_update_state.py extract-version <SKILL.md>
```

---

## 🧪 Testing

```bash
python -X utf8 -m unittest discover -s tests -v
```

Tests cover registration, version tracking, hash verification, fast updates, conflicts, backups, restore, migration, and GitHub URL validation.

---

## 🗂️ Repository Structure

```text
SKILL.md
agents/openai.yaml
scripts/skill_update_state.py
references/protocol.md
tests/
```

SkillFather currently manages one `SKILL.md` file per registry entry. Full Skill-directory synchronization is not yet supported.

---

## ⭐ Support

If SkillFather helps you manage your Skills, consider giving the repository a star.

It helps more Codex and Claude Code users discover the project.

---

## 📄 License

Released under the [MIT License](LICENSE).

<div align="center">

### 🧔 SkillFather

**Strict with updates. Protective of your local work.**

</div>
