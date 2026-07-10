import json
import tempfile
import unittest
from pathlib import Path

from scripts import skill_update_state as state


class SkillUpdateStateTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.registry = self.root / "registry.json"
        self.local = self.root / "local-skill" / "SKILL.md"
        self.local.parent.mkdir()
        self.candidate = self.root / "candidate.md"

    def tearDown(self):
        self.temp_dir.cleanup()

    def read_registry(self):
        return json.loads(self.registry.read_text(encoding="utf-8"))

    def stage(self):
        return state.stage_candidate(
            registry_path=self.registry,
            name="demo-skill",
            local_path=self.local,
            upstream_url="https://github.com/example/demo/blob/main/SKILL.md",
            upstream_ref="main",
            candidate_path=self.candidate,
            commit_sha="a" * 40,
            latest_version="v2",
        )

    def test_legacy_registry_migrates_without_losing_entries(self):
        legacy_hash = "a" * 64
        self.registry.write_text(
            json.dumps(
                [
                    {
                        "name": "demo-skill",
                        "local_path": str(self.local),
                        "status": "待更新",
                        "base_snapshot": "skill-update-snapshots/demo-skill/base.md",
                        "base_hash": legacy_hash,
                        "latest_hash": legacy_hash,
                        "first_diff_required": False,
                        "pending_conflicts": [],
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        migrated, changed = state.load_registry(self.registry)

        self.assertTrue(changed)
        self.assertEqual(2, migrated["schema_version"])
        self.assertEqual("update_available", migrated["skills"][0]["status"])
        self.assertEqual(f"sha256:{legacy_hash}", migrated["skills"][0]["base_hash"])
        self.assertTrue(migrated["skills"][0]["first_diff_required"])

    def test_mutating_legacy_registry_requires_explicit_migration(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        original = json.dumps(
            [
                {
                    "name": "demo-skill",
                    "local_path": str(self.local),
                    "status": "无更新",
                    "pending_conflicts": [],
                }
            ],
            ensure_ascii=False,
        )
        self.registry.write_text(original, encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "migration required"):
            self.stage()

        self.assertEqual(original, self.registry.read_text(encoding="utf-8"))

    def test_legacy_registry_accepts_utf8_bom(self):
        payload = json.dumps(
            [
                {
                    "name": "demo-skill",
                    "local_path": str(self.local),
                    "status": "无更新",
                    "pending_conflicts": [],
                }
            ],
            ensure_ascii=False,
        ).encode("utf-8")
        self.registry.write_bytes(b"\xef\xbb\xbf" + payload)

        migrated, changed = state.load_registry(self.registry)

        self.assertTrue(changed)
        self.assertEqual("demo-skill", migrated["skills"][0]["name"])

    def test_future_registry_schema_is_rejected(self):
        self.registry.write_text(
            json.dumps({"schema_version": 3, "skills": []}),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "newer registry schema"):
            state.load_registry(self.registry)

    def test_existing_legacy_entry_still_requires_approval_when_content_matches(self):
        self.local.write_text("same\n", encoding="utf-8")
        self.candidate.write_text("same\n", encoding="utf-8")
        content_hash = state.sha256_file(self.local)
        self.registry.write_text(
            json.dumps(
                [
                    {
                        "name": "demo-skill",
                        "local_path": str(self.local),
                        "upstream_url": "https://github.com/example/demo/blob/main/SKILL.md",
                        "upstream_ref": "main",
                        "base_snapshot": "skill-update-snapshots/demo-skill/base.md",
                        "base_hash": content_hash,
                        "latest_hash": content_hash,
                        "status": "无更新",
                        "pending_conflicts": [],
                    }
                ]
            ),
            encoding="utf-8",
        )
        state.migrate_registry(self.registry)

        entry = self.stage()

        self.assertTrue(entry["first_diff_required"])
        self.assertEqual("review_required", entry["status"])

        state.approve_candidate(
            self.registry,
            "demo-skill",
            entry["candidate_hash"],
            entry["local_hash_at_check"],
        )
        finalized = state.finalize_candidate(
            self.registry, "demo-skill", entry["candidate_hash"]
        )
        self.assertFalse(finalized["first_diff_required"])
        self.assertEqual("no_update", finalized["status"])

    def test_restage_matching_local_cannot_bypass_open_conflict(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        entry = self.stage()
        state.add_conflict(
            self.registry,
            "demo-skill",
            conflict_id="demo-skill#output#1",
            section="output",
            base_hash=None,
            local_hash=entry["local_hash_at_check"],
            candidate_hash=entry["candidate_hash"],
        )
        self.local.write_text("upstream\n", encoding="utf-8")

        restaged = self.stage()

        self.assertIsNone(restaged["base_snapshot"])
        self.assertIsNone(restaged["base_hash"])
        self.assertTrue(restaged["first_diff_required"])
        self.assertEqual("conflict", restaged["status"])

    def test_first_registration_with_different_local_requires_review(self):
        self.local.write_text("local customization\n", encoding="utf-8")
        self.candidate.write_text("upstream content\n", encoding="utf-8")

        entry = self.stage()

        self.assertTrue(entry["first_diff_required"])
        self.assertIsNone(entry["base_snapshot"])
        self.assertIsNone(entry["base_hash"])
        self.assertEqual("review_required", entry["status"])
        self.assertTrue((self.registry.parent / entry["candidate_snapshot"]).is_file())

    def test_first_registration_with_identical_content_accepts_base(self):
        self.local.write_text("same\n", encoding="utf-8")
        self.candidate.write_text("same\n", encoding="utf-8")

        entry = self.stage()

        self.assertFalse(entry["first_diff_required"])
        self.assertEqual("no_update", entry["status"])
        self.assertEqual(entry["candidate_hash"], entry["base_hash"])
        self.assertTrue((self.registry.parent / entry["base_snapshot"]).is_file())

    def test_finalize_refuses_to_advance_base_with_open_conflicts(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        entry = self.stage()
        state.add_conflict(
            self.registry,
            "demo-skill",
            conflict_id="demo-skill#output#1",
            section="output",
            base_hash=None,
            local_hash=entry["local_hash_at_check"],
            candidate_hash=entry["candidate_hash"],
        )

        with self.assertRaisesRegex(ValueError, "unresolved conflicts"):
            state.finalize_candidate(self.registry, "demo-skill", entry["candidate_hash"])

    def test_resolving_one_of_two_conflicts_keeps_conflict_status(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        entry = self.stage()
        for index in (1, 2):
            state.add_conflict(
                self.registry,
                "demo-skill",
                conflict_id=f"demo-skill#section-{index}#{index}",
                section=f"section-{index}",
                base_hash=None,
                local_hash=entry["local_hash_at_check"],
                candidate_hash=entry["candidate_hash"],
            )

        partly_resolved = state.resolve_conflict(
            self.registry,
            "demo-skill",
            "demo-skill#section-1#1",
            "keep-local",
        )
        fully_resolved = state.resolve_conflict(
            self.registry,
            "demo-skill",
            "demo-skill#section-2#2",
            "keep-local",
        )

        self.assertEqual("conflict", partly_resolved["status"])
        self.assertEqual("review_required", fully_resolved["status"])

    def test_finalize_advances_only_the_pinned_candidate(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        entry = self.stage()
        state.approve_candidate(
            self.registry,
            "demo-skill",
            entry["candidate_hash"],
            entry["local_hash_at_check"],
        )

        finalized = state.finalize_candidate(
            self.registry, "demo-skill", entry["candidate_hash"]
        )

        self.assertEqual(entry["candidate_hash"], finalized["base_hash"])
        self.assertFalse(finalized["first_diff_required"])
        self.assertEqual("no_update", finalized["status"])
        self.assertTrue((self.registry.parent / finalized["base_snapshot"]).is_file())

    def test_finalize_requires_recorded_approval(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        entry = self.stage()

        with self.assertRaisesRegex(ValueError, "approval required"):
            state.finalize_candidate(self.registry, "demo-skill", entry["candidate_hash"])

    def test_finalize_rejects_local_changes_after_approval(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        entry = self.stage()
        state.approve_candidate(
            self.registry,
            "demo-skill",
            entry["candidate_hash"],
            entry["local_hash_at_check"],
        )
        self.local.write_text("changed after approval\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "local file changed after approval"):
            state.finalize_candidate(self.registry, "demo-skill", entry["candidate_hash"])

    def test_finalize_rejects_mutated_candidate_snapshot(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        entry = self.stage()
        state.approve_candidate(
            self.registry,
            "demo-skill",
            entry["candidate_hash"],
            entry["local_hash_at_check"],
        )
        snapshot = self.registry.parent / entry["candidate_snapshot"]
        snapshot.write_text("tampered\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "candidate snapshot hash mismatch"):
            state.finalize_candidate(self.registry, "demo-skill", entry["candidate_hash"])

    def test_stage_requires_a_full_commit_sha(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "commit_sha"):
            state.stage_candidate(
                registry_path=self.registry,
                name="demo-skill",
                local_path=self.local,
                upstream_url="https://github.com/example/demo/blob/main/SKILL.md",
                upstream_ref="main",
                candidate_path=self.candidate,
                commit_sha=None,
            )

        with self.assertRaisesRegex(ValueError, "commit_sha"):
            state.stage_candidate(
                registry_path=self.registry,
                name="demo-skill",
                local_path=self.local,
                upstream_url="https://github.com/example/demo/blob/main/SKILL.md",
                upstream_ref="main",
                candidate_path=self.candidate,
                commit_sha="abc123",
            )

    def test_stage_rejects_non_skill_local_filename(self):
        wrong_local = self.root / "README.md"
        wrong_local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "SKILL.md"):
            state.stage_candidate(
                registry_path=self.registry,
                name="demo-skill",
                local_path=wrong_local,
                upstream_url="https://github.com/example/demo/blob/main/SKILL.md",
                upstream_ref="main",
                candidate_path=self.candidate,
                commit_sha="a" * 40,
            )

    def test_stage_rejects_registered_identity_change(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        self.stage()
        other_local = self.root / "other-skill" / "SKILL.md"
        other_local.parent.mkdir()
        other_local.write_text("local\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "identity"):
            state.stage_candidate(
                registry_path=self.registry,
                name="demo-skill",
                local_path=other_local,
                upstream_url="https://github.com/example/demo/blob/main/SKILL.md",
                upstream_ref="main",
                candidate_path=self.candidate,
                commit_sha="a" * 40,
            )

    def test_restore_creates_a_pre_restore_backup(self):
        self.local.write_text("current\n", encoding="utf-8")
        self.candidate.write_text("current\n", encoding="utf-8")
        self.stage()
        backup_dir = self.local.parent / ".skill-update-backups"
        backup_dir.mkdir()
        old_backup = backup_dir / "old.bak"
        old_backup.write_text("old\n", encoding="utf-8")

        pre_restore = state.restore_backup(self.registry, "demo-skill", old_backup)

        self.assertEqual("old\n", self.local.read_text(encoding="utf-8"))
        self.assertEqual("current\n", pre_restore.read_text(encoding="utf-8"))
        self.assertEqual("review_required", self.read_registry()["skills"][0]["status"])

    def test_restore_rejects_files_outside_managed_backup_directory(self):
        self.local.write_text("current\n", encoding="utf-8")
        self.candidate.write_text("current\n", encoding="utf-8")
        self.stage()
        outside = self.root / "outside.bak"
        outside.write_text("untrusted\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "managed backup"):
            state.restore_backup(self.registry, "demo-skill", outside)

    def test_mark_failure_preserves_the_last_candidate(self):
        self.local.write_text("local\n", encoding="utf-8")
        self.candidate.write_text("upstream\n", encoding="utf-8")
        entry = self.stage()

        failed = state.mark_failure(self.registry, "demo-skill", "network unavailable")

        self.assertEqual("check_failed", failed["status"])
        self.assertEqual("network unavailable", failed["last_error"])
        self.assertEqual(entry["candidate_hash"], failed["candidate_hash"])

    def test_list_entries_is_stable_and_sorted(self):
        self.local.write_text("same\n", encoding="utf-8")
        self.candidate.write_text("same\n", encoding="utf-8")
        self.stage()

        entries = state.list_entries(self.registry)

        self.assertEqual(["demo-skill"], [entry["name"] for entry in entries])

    def test_build_pinned_raw_url_rejects_untrusted_hosts(self):
        with self.assertRaisesRegex(ValueError, "github.com"):
            state.build_pinned_raw_url(
                "https://example.com/owner/repo/blob/main/SKILL.md",
                "main",
                "b" * 40,
            )

    def test_build_pinned_raw_url_uses_commit_sha(self):
        url = state.build_pinned_raw_url(
            "https://github.com/example/demo/blob/main/path/SKILL.md",
            "main",
            "b" * 40,
        )

        self.assertEqual(
            f"https://raw.githubusercontent.com/example/demo/{'b' * 40}/path/SKILL.md",
            url,
        )


if __name__ == "__main__":
    unittest.main()
