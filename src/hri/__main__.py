"""Allow ``python3 -m hri`` as an alternative to the ``hri`` command."""

from .cli import main

raise SystemExit(main())
