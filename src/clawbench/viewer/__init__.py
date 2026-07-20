"""Server-rendered Amazon WebsiteBench site and review workspace."""

from .app import create_app
from .discovery import CorpusIndex, discover_corpus

__all__ = ["CorpusIndex", "create_app", "discover_corpus"]
