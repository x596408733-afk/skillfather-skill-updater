# SkillFather | Skill Updater

SkillFather safely updates locally customized Codex or Claude `SKILL.md` files from GitHub. It pins upstream content to a commit, uses three-way comparison, requires a dry run and confirmation, protects local edits, records conflicts, and creates reversible backups.

## Safety model

- GitHub content is untrusted comparison data, not executable instruction.
- A registration snapshot is not accepted as base when local content differs.
- Every candidate is pinned by commit SHA and SHA-256.
- Customized local files are never rewritten wholesale; unchanged files may use the guarded fast path after backup and hash checks.
- Conflicts prevent base advancement.
- Candidate and final local hashes require a recorded two-phase approval before base advancement.
- Restore creates a pre-restore backup.
- Registered local path, GitHub URL, and ref cannot be silently retargeted.

## Install

Clone the repository into the personal Skills directory.

Codex on Windows:

```powershell
git clone https://github.com/x596408733-afk/skillfather-skill-updater.git "$env:USERPROFILE\.codex\skills\skillfather-skill-updater"
```

Codex on macOS/Linux:

```bash
git clone https://github.com/x596408733-afk/skillfather-skill-updater.git ~/.codex/skills/skillfather-skill-updater
```

Claude Code on macOS/Linux:

```bash
git clone https://github.com/x596408733-afk/skillfather-skill-updater.git ~/.claude/skills/skillfather-skill-updater
```

Restart or reload the agent after installation. Python 3.8+ and Git are required; the state helper uses only the Python standard library.

## Use

Invoke the Skill naturally or with its command family:

```text
Use $skillfather-skill-updater to register my local Skills.
/skill-update
/skill-update inventory
/skill-update fast all
/skill-update fast <name>
/skill-update register <local-skill-path> <github-blob-url>
/skill-update list
/skill-update check all
/skill-update diff <name>
/skill-update merge <name>
/skill-update resolve <conflict-id>
/skill-update backup <name>
/skill-update restore <name> <backup-path>
```

The Skill manages one `SKILL.md` per registry entry. It does not currently synchronize a Skill's supporting directories.

## Skill inventory

Bare `/skill-update` shows a local dashboard immediately, refreshes registered upstreams in repository groups, then shows the final dashboard. Every completed workflow uses:

| Skill | Type | GitHub address | Current version | Latest version | Update eligibility |
| --- | --- | --- | --- | --- | --- |

Eligibility is displayed as `Yes`, `No`, `Review required`, `Check failed`, `Cannot check`, or `Managed by Codex`. An unregistered Skill with no verified local GitHub origin shows `无`; an unavailable version shows `未知`.

`/skill-update fast` updates only files whose current hash still equals their accepted base. Each eligible file is backed up and rechecked before the pinned candidate is copied. A customized, conflicted, first-review, stale, or unregistered Skill automatically returns to guarded review and still requires a separate merge confirmation.

Checks resolve one commit per repository/ref group, process at most four groups concurrently, reuse valid unchanged candidates, and isolate failures. Retrieval tries the GitHub Contents API, `raw.githubusercontent.com`, then shallow Git while keeping one pinned commit SHA.

## Test

```bash
python -X utf8 -m unittest discover -s tests -v
```

The tests cover legacy registry migration, first registration, immutable candidates, conflict-gated finalization, backups, restore, GitHub URL pinning, and package metadata.

## Repository layout

```text
SKILL.md                         Agent workflow
agents/openai.yaml               Codex UI metadata
scripts/skill_update_state.py    Deterministic state helper
references/protocol.md           Registry and merge protocol
tests/                           Standard-library test suite
```

Licensed under the MIT License.
