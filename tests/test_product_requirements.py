"""Executable traceability checks for the Rust-authority product baseline."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIREMENT_ROW = re.compile(r"^\| `(NR-[A-Z]+-\d{3})` \|", re.MULTILINE)
TEST_ROW = re.compile(r"^\| `(T-[A-Z0-9]+-\d{3})` \| ([^|]+) \|", re.MULTILINE)
EXPLICIT_REQUIREMENT = re.compile(r"NR-[A-Z]+-\d{3}")
REQUIREMENT_RANGE = re.compile(r"(NR-[A-Z]+-)(\d{3})\.\.(\d{3})")


def _expand_coverage(value: str) -> set[str]:
    expanded = set(EXPLICIT_REQUIREMENT.findall(value))
    for prefix, start, end in REQUIREMENT_RANGE.findall(value):
        expanded.update(
            f"{prefix}{number:03d}"
            for number in range(int(start), int(end) + 1)
        )
    return expanded


class ProductRequirementTraceabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.requirements_text = (ROOT / "docs" / "requirements.md").read_text(
            encoding="utf-8"
        )
        cls.matrix_text = (ROOT / "docs" / "verification-matrix.md").read_text(
            encoding="utf-8"
        )
        cls.orchestrator_plan_text = (
            ROOT / "docs" / "ga-completion-orchestrator-plan.md"
        ).read_text(encoding="utf-8")

    def test_requirement_ids_are_unique_and_comprehensive(self) -> None:
        identifiers = REQUIREMENT_ROW.findall(self.requirements_text)
        self.assertGreaterEqual(len(identifiers), 50)
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_test_ids_are_unique(self) -> None:
        identifiers = [test_id for test_id, _ in TEST_ROW.findall(self.matrix_text)]
        self.assertGreaterEqual(len(identifiers), 15)
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_every_requirement_has_a_release_test(self) -> None:
        requirements = set(REQUIREMENT_ROW.findall(self.requirements_text))
        covered: set[str] = set()
        for _, coverage in TEST_ROW.findall(self.matrix_text):
            covered.update(_expand_coverage(coverage))
        self.assertEqual(requirements - covered, set())

    def test_baseline_has_no_placeholder_language(self) -> None:
        combined = self.requirements_text + self.matrix_text
        for marker in ("TODO", "TBD", "FIXME", "HACK", "XXX"):
            self.assertNotIn(marker, combined)

    def test_polyglot_adr_preserves_single_rust_authority(self) -> None:
        text = (ROOT / "docs" / "adr" / "0002-justified-polyglot-boundaries.md").read_text(
            encoding="utf-8"
        )
        for boundary in ("Python", "Go", "PostgreSQL SQL", "TypeScript", "PowerShell"):
            self.assertIn(boundary, text)
        self.assertIn("MUST NOT duplicate", text)

    def test_orchestrator_plan_covers_every_requirement_and_release_test(self) -> None:
        requirements = set(REQUIREMENT_ROW.findall(self.requirements_text))
        release_tests = {test_id for test_id, _ in TEST_ROW.findall(self.matrix_text)}
        self.assertEqual(
            requirements - set(EXPLICIT_REQUIREMENT.findall(self.orchestrator_plan_text)),
            set(),
        )
        plan_test_ids = set(re.findall(r"T-[A-Z0-9]+-\d{3}", self.orchestrator_plan_text))
        self.assertEqual(release_tests - plan_test_ids, set())
        for required_section in (
            "Multi-agent operating protocol",
            "Master work-package catalog",
            "Evidence envelope",
            "Explicit requirement-to-work-package coverage",
            "Final GA evidence bundle",
        ):
            self.assertIn(required_section, self.orchestrator_plan_text)


if __name__ == "__main__":
    unittest.main()
