"""Configuration for the PubMed E-utilities client."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from os import environ
from urllib.parse import urlsplit


class PubMedConfigurationError(ValueError):
    """Raised when PubMed client configuration is incomplete or invalid."""


_EUTILITIES_HOST = "eutils.ncbi.nlm.nih.gov"


@dataclass(frozen=True)
class PubMedClientConfig:
    """Validated operational settings for a PubMed E-utilities client."""

    tool: str
    email: str
    api_key: str | None = None
    timeout_seconds: float = 10.0
    requests_per_second: float | None = None
    base_url: str = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

    def __post_init__(self) -> None:
        tool = _normalise_required_text(self.tool, "tool")
        if any(character.isspace() for character in tool):
            raise PubMedConfigurationError("tool must not contain whitespace.")

        email = _normalise_required_text(self.email, "email")
        if "@" not in email:
            raise PubMedConfigurationError("email must be a valid developer email address.")

        api_key = _normalise_optional_text(self.api_key, "api_key")
        timeout_seconds = _normalise_positive_number(
            self.timeout_seconds, "timeout_seconds"
        )
        base_url = _normalise_base_url(self.base_url)

        maximum_rate = 10.0 if api_key else 3.0
        if self.requests_per_second is None:
            requests_per_second = maximum_rate
        else:
            requests_per_second = _normalise_positive_number(
                self.requests_per_second, "requests_per_second"
            )
            if requests_per_second > maximum_rate:
                raise PubMedConfigurationError(
                    "requests_per_second exceeds the supported NCBI request rate."
                )

        object.__setattr__(self, "tool", tool)
        object.__setattr__(self, "email", email)
        object.__setattr__(self, "api_key", api_key)
        object.__setattr__(self, "timeout_seconds", timeout_seconds)
        object.__setattr__(self, "requests_per_second", requests_per_second)
        object.__setattr__(self, "base_url", base_url)

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> PubMedClientConfig:
        """Build configuration from documented PubMed environment variables."""
        values = environ if environment is None else environment
        timeout = values.get("PUBMED_TIMEOUT_SECONDS", "10")
        request_rate = values.get("PUBMED_REQUESTS_PER_SECOND")

        try:
            timeout_seconds = float(timeout)
            requests_per_second = (
                float(request_rate) if request_rate is not None else None
            )
        except ValueError as error:
            raise PubMedConfigurationError(
                "PubMed timeout and request-rate environment values must be numbers."
            ) from error

        return cls(
            tool=values.get("PUBMED_NCBI_TOOL", ""),
            email=values.get("PUBMED_NCBI_EMAIL", ""),
            api_key=values.get("PUBMED_NCBI_API_KEY"),
            timeout_seconds=timeout_seconds,
            requests_per_second=requests_per_second,
            base_url=values.get(
                "PUBMED_EUTILITIES_BASE_URL",
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/",
            ),
        )


def _normalise_required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise PubMedConfigurationError(f"{field_name} must be text.")

    normalised = value.strip()
    if not normalised:
        raise PubMedConfigurationError(f"{field_name} must not be empty.")
    return normalised


def _normalise_optional_text(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _normalise_required_text(value, field_name)


def _normalise_positive_number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PubMedConfigurationError(f"{field_name} must be a positive number.")

    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise PubMedConfigurationError(f"{field_name} must be a positive number.")
    return number


def _normalise_base_url(value: object) -> str:
    if not isinstance(value, str):
        raise PubMedConfigurationError("base_url must be text.")

    base_url = value.strip()
    try:
        parsed = urlsplit(base_url)
    except ValueError as error:
        raise PubMedConfigurationError(
            "base_url must be a valid HTTPS E-utilities URL."
        ) from error

    if parsed.scheme != "https" or parsed.hostname != _EUTILITIES_HOST:
        raise PubMedConfigurationError(
            "base_url must use HTTPS on the official NCBI E-utilities host."
        )
    return f"{base_url.rstrip('/')}/"
