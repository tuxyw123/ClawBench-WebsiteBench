"""Manifest-driven infrastructure for evidence-scoped offline website clones."""

from .assets import AssetClosureReport, verify_asset_closure
from .manifest import (
    LoadedManifest,
    ManifestValidationError,
    load_coverage_ledger,
    load_manifest,
)
from .report import coverage_report

__all__ = [
    "AssetClosureReport",
    "LoadedManifest",
    "ManifestValidationError",
    "coverage_report",
    "load_coverage_ledger",
    "load_manifest",
    "verify_asset_closure",
]
