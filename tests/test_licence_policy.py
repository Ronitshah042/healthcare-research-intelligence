"""Tests for the full-text licence allowlist."""

import unittest

from ingestion.licence_policy import (
    DEFAULT_ALLOWED_LICENCES,
    LicencePolicy,
    LicencePolicyError,
    normalise_licence,
)


class LicencePolicyTests(unittest.TestCase):
    def test_default_allows_only_cc0_and_cc_by(self) -> None:
        policy = LicencePolicy.from_environment({})

        self.assertEqual(DEFAULT_ALLOWED_LICENCES, ("CC0", "CC BY"))
        for licence in ("CC0", "CC BY", "cc by", " CC  BY "):
            with self.subTest(allowed=licence):
                self.assertTrue(policy.allows(licence))
        for licence in (
            "CC BY-NC", "CC BY-ND", "CC BY-NC-ND", "CC BY-NC-SA", "CC BY-SA",
            "TDM", "CC-BY", "CC BY 4.0", "", "  ", None, 0, "unknown",
        ):
            with self.subTest(rejected=licence):
                self.assertFalse(policy.allows(licence))

    def test_reads_a_custom_allowlist_from_the_environment(self) -> None:
        policy = LicencePolicy.from_environment(
            {"HRI_ALLOWED_LICENCES": "CC0, cc by ,CC BY-SA,"}
        )

        self.assertEqual(policy.allowed, frozenset({"CC0", "CC BY", "CC BY-SA"}))
        self.assertTrue(policy.allows("CC BY-SA"))
        self.assertFalse(policy.allows("CC BY-NC"))

    def test_blank_setting_means_the_strict_default(self) -> None:
        self.assertEqual(
            LicencePolicy.from_environment({"HRI_ALLOWED_LICENCES": "  "}),
            LicencePolicy.of(DEFAULT_ALLOWED_LICENCES),
        )

    def test_rejects_an_allowlist_with_no_licences(self) -> None:
        with self.assertRaises(LicencePolicyError):
            LicencePolicy.from_environment({"HRI_ALLOWED_LICENCES": " , ,"})
        with self.assertRaises(LicencePolicyError):
            LicencePolicy.of([])
        with self.assertRaises(LicencePolicyError):
            LicencePolicy.of(["  "])

    def test_normalises_case_and_spaces_only(self) -> None:
        self.assertEqual(normalise_licence("  cc   by-nc "), "CC BY-NC")
        self.assertIsNone(normalise_licence(None))
        self.assertIsNone(normalise_licence(" "))


if __name__ == "__main__":
    unittest.main()
