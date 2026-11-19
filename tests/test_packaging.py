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
    # ``tomllib`` needs Python 3.11, so read the lists with patterns.
    text = (ROOT / "pyproject.toml").read_text()
    block = re.search(r"^dependencies = \[(.*?)\]", text, re.DOTALL | re.MULTILINE)
    assert block is not None, "pyproject.toml has no dependencies list"
    optional = re.findall(r"^\w+ = \[(.*?)\]", text.split(
        "[project.optional-dependencies]", 1)[-1].split("\n[", 1)[0],
        re.DOTALL | re.MULTILINE)
    return sorted(
        item for found in (block.group(1), *optional)
        for item in re.findall(r'"([^"]+)"', found)
    )


class PackagingTests(unittest.TestCase):
    def test_requirements_and_pyproject_list_the_same_dependencies(self) -> None:
        self.assertEqual(_requirements(), _pyproject_dependencies())

    def test_every_requirement_is_pinned_to_an_exact_version(self) -> None:
        for requirement in _requirements():
            with self.subTest(requirement=requirement):
                self.assertRegex(
                    requirement, r"^[A-Za-z0-9._-]+==[^=<>~!\s;]+(; python_version >= '[0-9.]+')?$"
                )

    def test_embedding_constraints_are_pinned_and_match_the_extra(self) -> None:
        lines = [
            line.strip()
            for line in (ROOT / "constraints-embeddings.txt").read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]
        for line in lines:
            with self.subTest(line=line):
                self.assertRegex(line, r"^[A-Za-z0-9._-]+==[^=<>~!\s;]+$")
        pins = {line.split("==")[0].lower(): line for line in lines}
        fastembed = next(r for r in _requirements() if r.startswith("fastembed=="))
        self.assertEqual(pins["fastembed"], fastembed.split(";")[0])
        for package in ("onnxruntime", "numpy", "tokenizers", "huggingface_hub"):
            self.assertIn(package, pins)

    def test_console_script_points_at_the_cli_entry_point(self) -> None:
        text = (ROOT / "pyproject.toml").read_text()
        self.assertIn('hri = "hri.cli:main"', text)
        from hri.cli import main  # the target must be importable

        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
