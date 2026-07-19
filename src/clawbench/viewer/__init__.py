"""Server-rendered WebsiteBench corpus QA viewer."""

from .app import create_app
from .discovery import CorpusIndex, discover_corpus

__all__ = ["CorpusIndex", "create_app", "discover_corpus"]
