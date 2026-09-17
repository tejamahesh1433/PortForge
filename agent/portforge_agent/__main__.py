"""Enables `python -m portforge_agent scan`."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
