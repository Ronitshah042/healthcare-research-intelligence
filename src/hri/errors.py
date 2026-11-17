"""The service layer's input error, shared by every service module."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager


class InvalidRequestError(ValueError):
    """Raised when a caller supplies an unusable argument."""


@contextmanager
def as_invalid_request() -> Iterator[None]:
    """Report lower-layer ``ValueError`` input failures as one service error."""
    try:
        yield
    except InvalidRequestError:
        raise
    except ValueError as error:
        raise InvalidRequestError(str(error)) from error
