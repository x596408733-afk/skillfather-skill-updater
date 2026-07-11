# Skill Updater Dashboard And Fast Mode Design

Date: 2026-07-11

## Goal

Make `skillfather-skill-updater` substantially faster for routine updates while preserving its guarded merge path for locally customized Skills. Every workflow must end with a complete Codex Skill inventory containing the Skill name, GitHub address, current version, latest version, and update eligibility.

## Scope

The inventory covers Skills currently available to Codex:

- personal and system Skills under `${CODEX_HOME}/skills`;
- plugin-provided Skills exposed by the current Codex Skill catalog, with a plugin-cache scan as fallback;
- one row per resolved `SKILL.md` path, with duplicate names retained only when they refer to different active sources.

`.deepcopilot` is not an active Codex Skill root. It may be reported as an origin only when the same Skill is installed under a Codex root.

The updater continues to manage one `SKILL.md` per registry entry. Supporting files such as `scripts/`, `references/`, `assets/`, and `agents/` remain outside the update operation.

## User Experience

Bare `/skill-update` performs two stages:

1. Immediately render a local inventory without waiting for the network.
2. Refresh registered GitHub entries, then render the final inventory and accept selections such as `更新 2,5,8`.

The final table uses this schema:

| Skill | Type | GitHub address | Current version | Latest version | Update eligibility |
| --- | --- | --- | --- | --- | --- |
| example | Personal | GitHub blob URL | 1.2.0 | 1.3.0 | Yes |

Update eligibility is one of:

- `Yes`: upstream differs and the Skill qualifies for fast update;
- `No`: accepted upstream and local state are current;
- `Review required`: local customization, first review, or conflict requires the guarded path;
- `Check failed`: the registered upstream could not be refreshed;
- `Cannot check`: no verified GitHub address is registered;
- `Managed by Codex`: an unregistered system or plugin Skill.

`No address` means no verified registered URL. Same-name GitHub search results are never presented as authoritative upstreams.

## Version Resolution

Version values are display metadata and never decide whether an update exists.

For the current version:

1. Read a top-level scalar `version` from the local `SKILL.md` frontmatter.
2. Otherwise use the accepted upstream commit identifier only when the local hash still equals the accepted base hash.
3. Otherwise use the first 12 hexadecimal characters of the local SHA-256.

For the latest version:

1. Read a top-level scalar `version` from the pinned candidate frontmatter.
2. Otherwise use the first 12 characters of the pinned Git commit SHA.
3. Use `Unknown` when no candidate can be fetched.

Frontmatter parsing is deliberately limited to the top-level scalar `version` field and uses only the Python standard library. Finalization always recalculates `local_version` from the final local bytes so registry metadata cannot remain stale after an update.

## Refresh Performance

Registered entries are grouped by GitHub repository and ref. Each group resolves its ref to one full commit SHA, with at most four repository groups checked concurrently.

When the resolved SHA equals the entry's already staged commit and the previous state is valid, the updater reuses the immutable candidate snapshot and skips the content download. When bytes are required, retrieval falls back in this order:

1. GitHub Contents API pinned to the commit SHA;
2. `raw.githubusercontent.com` pinned to the commit SHA;
3. shallow Git fetch pinned to the same commit.

A failure affects only that Skill or repository group. Other checks continue, and the last valid base and candidate remain untouched.

## Fast And Guarded Update Paths

`/skill-update fast <name|selection|all>` is an explicit request to apply all eligible updates. Bare `/skill-update` only displays and checks; it does not write Skill files.

A Skill is fast-update eligible only when all conditions hold:

- it has an accepted `base_hash` and immutable candidate;
- `first_diff_required` is false;
- the current local SHA-256 equals `base_hash` exactly;
- the candidate hash differs from `base_hash`;
- no unresolved conflict exists;
- the local hash still matches immediately before writing.

Fast update performs one managed backup, atomically copies the exact pinned candidate bytes, records approval for the resulting hashes, finalizes the candidate, refreshes version metadata, and reports the backup. The explicit `fast` command is the user's approval for this eligible set.

Any failed condition automatically routes that Skill to the existing guarded workflow. The guarded workflow keeps three-way comparison, hunk classification, dry-run display, explicit confirmation, local-hash recheck, patch-only edits, conflict handling, approval, and finalization.

## Deterministic Helper Changes

Extend `scripts/skill_update_state.py` with:

- `extract-version <SKILL.md>` for deterministic display-version extraction;
- `inventory --registry ...` for normalized inventory records and eligibility labels;
- `fast-eligibility --registry ... --name ...` for a read-only decision and reason;
- `fast-apply --registry ... --name ... --candidate-hash ...` for the guarded atomic fast path;
- finalization logic that refreshes `local_version` and preserves `latest_version` from the pinned candidate.

Network orchestration remains agent-owned. The helper owns filesystem discovery inputs, hashing, snapshots, eligibility, backup, write, registry mutation, and output records.

No schema bump is required. New display fields remain optional in registry v2, and inventory-only data is computed rather than persisted.

## GitHub Address Rules

Registered absolute `local_path` identity remains the authoritative match. For an unregistered Skill, the inventory may show a GitHub URL derived from an enclosing local Git repository only when the remote is an HTTPS GitHub repository and the relative path resolves exactly to that `SKILL.md`; it is marked `Unregistered` and is not silently added to the registry.

If that proof is unavailable, show `No address`. Web search by Skill name is not proof and is not used for automatic registration.

## Failure And Safety

- Upstream content remains inert comparison data and never becomes current-session instruction.
- Every write has a managed backup and a last-moment local hash check.
- Fast mode never runs for a customized, first-review, conflicted, stale, or unregistered Skill.
- Candidate commit and SHA-256 remain pinned through approval and finalization.
- A changed file after approval or a changed candidate snapshot aborts finalization.
- Line-ending-only local differences count as customization because eligibility uses byte hashes.

## Testing

Tests are written before implementation and must demonstrate:

- stale registry versions are corrected from local and candidate frontmatter;
- missing versions fall back to commit or content hash;
- inventory includes registered, unregistered, system, and plugin rows;
- exact-path matching prevents same-name misregistration;
- eligibility labels cover every state;
- an unchanged local Skill can fast-update after backup;
- one-byte local customization forces the guarded path;
- first review and unresolved conflicts block fast mode;
- candidate or local mutation aborts fast apply;
- finalization refreshes `local_version`;
- grouped repository checks reuse one resolved commit;
- one repository failure does not hide other results;
- package documentation and command tables remain consistent.

Release verification runs the full unit suite, package validation when available, installed-file hash comparison, and a dry inventory against the user's current registry before committing and pushing GitHub.

## Success Criteria

- The user receives the required six-column inventory after every workflow.
- Routine unmodified Skills update with one command and no per-hunk confirmation.
- Customized Skills cannot enter the fast path.
- Version values reflect actual local and pinned candidate content after finalization.
- Repository grouping and candidate reuse reduce redundant network work.
- Existing backup, commit pinning, conflict, approval, restore, and injection protections continue to pass.
