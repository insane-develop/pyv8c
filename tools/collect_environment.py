"""CLI wrapper for the packaged browser environment collector."""

import sys

from pyv8c.tools.collect_environment import main


if __name__ == "__main__":
    sys.exit(main())
