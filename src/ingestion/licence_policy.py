"""Which article licences allow full text to be stored (docs/CONTENT_POLICY.md)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from os import environ


ALLOWED_LICENCES_VARIABLE = "HRI_ALLOWED_LICENCES"
DEFAULT_ALLOWED_LICENCES = ("CC0", "CC BY")


class LicencePolicyError(ValueError):
    """Raised when the configured licence allowlist is unusable."""


def normalise_licence(value: object) -> str | None:
    """Return a licence code in comparable form, or ``None`` when missing.

    Only letter case and repeated spaces are ignored, so ``cc  by`` equals
    ``CC BY`` but ``CC-BY`` and ``CC BY 4.0`` do not.
    """
    if not isinstance(value, str):
        return None
    normalised = " ".join(value.split()).upper()
    return normalised or None


@dataclass(frozen=True)
class LicencePolicy:
    """An allowlist of licence codes; anything else is rejected."""

    allowed: frozenset[str]

    def __post_init__(self) -> None:
        if not self.allowed or None in self.allowed:
            raise LicencePolicyError("At least one allowed licence is required.")

    @classmethod
    def of(cls, licences: Iterable[str]) -> LicencePolicy:
        return cls(frozenset(normalise_licence(licence) for licence in licences))

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> LicencePolicy:
        """Read ``HRI_ALLOWED_LICENCES``; blank or unset means the strict default."""
        values = environ if environment is None else environment
        configured = values.get(ALLOWED_LICENCES_VARIABLE, "").strip()
        if not configured:
            return cls.of(DEFAULT_ALLOWED_LICENCES)
        licences = [part for part in configured.split(",") if part.strip()]
        if not licences:
            raise LicencePolicyError(
                f"{ALLOWED_LICENCES_VARIABLE} must list at least one licence."
            )
        return cls.of(licences)

    def allows(self, licence: object) -> bool:
        """Return whether text under ``licence`` may be stored."""
        normalised = normalise_licence(licence)
        return normalised is not None and normalised in self.allowed
