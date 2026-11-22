"""Check that the two dependency lists describe the same packages."""

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _requirements(name: str = "requirements.txt") -> list[str]:
    lines = (ROOT / name).read_text().splitlines()
    return sorted(
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    )


def _extras(*names: str) -> list[str]:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert project["dependencies"] == [], "the core must stay dependency-free"
    extras = project["optional-dependencies"]
    return sorted(item for name in names for item in extras[name])


class PackagingTests(unittest.TestCase):
    def test_requirement_files_match_the_pyproject_extras(self) -> None:
        self.assertEqual(_requirements(), _extras("embeddings", "app"))
        self.assertEqual(_requirements("requirements-dev.txt"), _extras("api", "dev"))

    def test_the_runtime_has_no_pytorch(self) -> None:
        for name in ("requirements.txt", "requirements-dev.txt"):
            self.assertFalse([r for r in _requirements(name) if r.startswith("torch")])

    def test_every_requirement_is_pinned_to_an_exact_version(self) -> None:
        for requirement in _requirements() + _requirements("requirements-dev.txt"):
            with self.subTest(requirement=requirement):
                self.assertRegex(
                    requirement, r"^[A-Za-z0-9._-]+==[^=<>~!\s;]+$"
                )

    def test_deploy_constraints_pin_the_runtime_versions(self) -> None:
        lines = [line.strip() for line in (ROOT / "constraints-deploy.txt").read_text().splitlines()
                 if line.strip() and not line.startswith("#")]
        for line in lines:
            with self.subTest(line=line):
                self.assertRegex(line, r"^[A-Za-z0-9._-]+==[^=<>~!\s;]+$")
        pins = {line.split("==")[0].lower(): line for line in lines}
        for requirement in _requirements():
            self.assertEqual(pins[requirement.split("==")[0].lower()], requirement)
        self.assertNotIn("torch", pins)

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
        self.assertEqual(pins["fastembed"], fastembed)
        for package in ("onnxruntime", "numpy", "tokenizers", "huggingface_hub"):
            self.assertIn(package, pins)

    def test_console_script_points_at_the_cli_entry_point(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        self.assertEqual(project["scripts"]["hri"], "hri.cli:main")
        from hri.cli import main  # the target must be importable

        self.assertTrue(callable(main))


if __name__ == "__main__":
    unittest.main()
