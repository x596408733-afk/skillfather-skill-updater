import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillPackageTests(unittest.TestCase):
    def test_public_package_contains_required_files(self):
        required = [
            "SKILL.md",
            "agents/openai.yaml",
            "scripts/skill_update_state.py",
            "references/protocol.md",
            "README.md",
            "LICENSE",
        ]
        for relative_path in required:
            with self.subTest(path=relative_path):
                self.assertTrue((ROOT / relative_path).is_file())

    def test_skill_metadata_and_commands_are_consistent(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertRegex(skill, r"(?m)^name: skillfather-skill-updater$")
        self.assertRegex(skill, r"(?m)^description: Use when ")
        for command in (
            "register",
            "list",
            "check",
            "diff",
            "merge",
            "resolve",
            "backup",
            "restore",
        ):
            self.assertIn(f"/skill-update {command}", skill)
        self.assertIn("references/protocol.md", skill)
        self.assertIn("approve", skill)
        frontmatter_name = re.search(r"(?m)^name:\s*([a-z0-9-]+)$", skill).group(1)
        self.assertEqual(frontmatter_name, ROOT.name)

        protocol = (ROOT / "references" / "protocol.md").read_text(encoding="utf-8")
        self.assertIn('"approved_candidate_hash"', protocol)
        self.assertIn('"approved_local_hash"', protocol)
        self.assertIn('"$STATE" approve', protocol)
        self.assertIn("registered identity", protocol)
        self.assertIn("newer schema", protocol)
        self.assertIn("managed backup directory", protocol)

    def test_openai_metadata_invokes_the_new_name(self):
        metadata = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn('display_name: "SkillFather | Skill Updater"', metadata)
        self.assertIn("$skillfather-skill-updater", metadata)
        short = re.search(r'short_description:\s*"([^"]+)"', metadata).group(1)
        self.assertGreaterEqual(len(short), 25)
        self.assertLessEqual(len(short), 64)

    def test_completion_inventory_contract_is_documented(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        protocol = (ROOT / "references" / "protocol.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        for document in (skill, protocol, readme):
            self.assertIn("Skill inventory", document)
            self.assertIn("unregistered Skill", document)
            self.assertIn("`无`", document)

    def test_dashboard_fast_update_contract_is_documented(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        protocol = (ROOT / "references" / "protocol.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("`/skill-update`", skill)
        self.assertIn("/skill-update inventory", skill)
        self.assertIn("/skill-update fast", skill)
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
        self.assertIn("fast-eligibility", protocol)
        self.assertIn("fast-apply", protocol)


if __name__ == "__main__":
    unittest.main()
