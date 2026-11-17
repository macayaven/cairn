"""cairn: a provenance-first memory compiler for AI agents."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("cairn-memory")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0+unknown"

SOFTWARE = "cairn"  # how the software names itself with no brand: a page's frame, a sentence
