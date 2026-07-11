# Dashboard And Fast Mode Behavior Evidence

Date: 2026-07-11

## RED Baseline

A fresh read-only worker evaluated the pre-change `SKILL.md` and protocol.

### Bare `/skill-update`

RED. The old contract had no bare dashboard command, no Skill type, no current/latest version rules, and no update-eligibility column. It required only the Skill name and registered GitHub URL.

### `/skill-update fast all`

RED. The old contract had no fast command or `local_hash == base_hash` eligibility rule. Every file required the guarded dry-run and separate confirmation.

### Grouped `/skill-update check all`

RED. The old contract processed entries independently, could resolve one repository multiple times, had no concurrency limit, no Contents API or shallow-Git fallback, and no explicit batch failure isolation.

## GREEN Evidence

### Complete Dashboard

The package contract requires bare `/skill-update`, `/skill-update inventory`, all six columns, all six eligibility labels, and explicit address/version fallback values. A disposable copy of the real registry produced valid seven-field machine rows for 88 installed Skills: 48 personal, 5 system, and 35 plugin Skills. The real registry SHA-256 was unchanged.

### Fast Update And Downgrade

Unit tests prove that an unchanged local Skill is backed up, atomically updated from the pinned candidate, approved, finalized, and assigned its new version. Separate tests prove that a one-byte local customization returns `local_differs_from_base` and a mutated candidate snapshot aborts before writing.

### Grouping, Concurrency, And Fallback

The Skill and protocol require repository/ref grouping, one ref resolution per group, at most four concurrent groups, candidate reuse, Contents API then `raw.githubusercontent.com` then shallow Git fallback, and continuation after an isolated group failure. Package tests fail if these rules disappear.

## Verification

- State and package suite: 55 tests passed, no skips.
- Disposable real-registry inventory: 88 complete rows.
- `patent-disclosure-skill`: current `1.8.9`, latest `1.8.9`, eligibility `no`.
- Real registry mutation: none; SHA-256 unchanged.
