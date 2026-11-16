"""Check that the two dependency lists describe the same packages."""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _requirements() -> list[str]:
    lines = (ROOT / "requirements.txt").read_text().splitlines()
    return sorted(
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    )


def _pyproject_dependencies() -> list[str]:
    # ``tomllib`` needs Python 3.11, so read the one list with a pattern.
    text = (ROOT / "pyproject.toml").read_text()
    block = re.search(r"^dependencies = \[(.*?)\]", text, re.DOTALL | re.MULTILINE)
    assert block is not None, "pyproject.toml has no dependencies list"
    return sorted(re.findall(r'"([^"]+)"', block.group(1)))


class PackagingTests(unittest.TestCase):
    def test_requirements_and_pyproject_list_the_same_dependencies(self) -> None:
        self.assertEqual(_requirements(), _pyproject_dependencies())

    def test_every_requirement_is_pinned_to_an_exact_version(self) -> None:
        for requirement in _requirements():
            with self.subTest(requirement=requirement):
                self.assertRegex(requirement, r"^[A-Za-z0-9._-]+==[^=<>~!\s]+$")

    def test_console_script_points_at_the_cli_entry_point(self) -> None:
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn('hri = "hri.cli:main"', text)
        from hri.cli import main  # the target must be importable

        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
