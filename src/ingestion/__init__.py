"""Components for preparing research material for ingestion."""

from .metadata import MetadataValidationError, PaperMetadata, normalise_paper_metadata
from .pubmed import (
    PUBMED_ATTRIBUTION,
    PublicationYearFilter,
    PubMedAttribution,
    PubMedClient,
    PubMedError,
    PubMedHttpError,
    PubMedRequestValidationError,
    PubMedResponseError,
    PubMedSearchPage,
    PubMedSearchRecord,
    PubMedTimeoutError,
    PubMedTransportError,
    SearchMode,
)
from .pubmed_config import PubMedClientConfig, PubMedConfigurationError

__all__ = [
    "MetadataValidationError",
    "PaperMetadata",
    "normalise_paper_metadata",
    "PUBMED_ATTRIBUTION",
    "PublicationYearFilter",
    "PubMedAttribution",
    "PubMedClient",
    "PubMedClientConfig",
    "PubMedConfigurationError",
    "PubMedError",
    "PubMedHttpError",
    "PubMedRequestValidationError",
    "PubMedResponseError",
    "PubMedSearchPage",
    "PubMedSearchRecord",
    "PubMedTimeoutError",
    "PubMedTransportError",
    "SearchMode",
]
