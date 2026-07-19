"""Diagnostic image metrics. These values are never official WebsiteBench scores."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


STABILITY_NMAE_THRESHOLD = 0.003
STABILITY_CHANGED_PIXEL_THRESHOLD = 0.02
STABILITY_PIXEL_DELTA = 8
VISUAL_CONTENT_PIXEL_DELTA = 8
VISUAL_CONTENT_CHANGED_PIXEL_MIN = 0.002
VISUAL_CONTENT_RGB_STD_MIN = 4.0


def _imports() -> tuple[Any, Any, Any]:
    try:
        import numpy as np
        from PIL import Image
        from skimage import color, feature, metrics, morphology
    except ImportError as exc:
        raise RuntimeError(
            "visual diagnostics require the clawbench-websitebench image dependencies"
        ) from exc
    return np, Image, (color, feature, metrics, morphology)


def _load_rgb(path: Path) -> Any:
    np, Image, _ = _imports()
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"), dtype=np.uint8)


def _apply_regions(source: Any, candidate: Any, regions: list[dict[str, int]]) -> tuple[Any, Any]:
    source = source.copy()
    candidate = candidate.copy()
    for region in regions:
        x0, y0 = region["x"], region["y"]
        x1 = min(x0 + region["width"], source.shape[1])
        y1 = min(y0 + region["height"], source.shape[0])
        source[y0:y1, x0:x1] = 255
        candidate[y0:y1, x0:x1] = 255
    return source, candidate


def _edge_f1(source: Any, candidate: Any) -> float:
    np, _, libraries = _imports()
    color, feature, _, morphology = libraries
    source_edges = feature.canny(color.rgb2gray(source), sigma=1.2)
    candidate_edges = feature.canny(color.rgb2gray(candidate), sigma=1.2)
    radius = morphology.disk(1)
    source_total = int(np.count_nonzero(source_edges))
    candidate_total = int(np.count_nonzero(candidate_edges))
    if source_total == candidate_total == 0:
        return 1.0
    if source_total == 0 or candidate_total == 0:
        return 0.0
    precision = float(np.count_nonzero(candidate_edges & morphology.dilation(source_edges, radius))) / candidate_total
    recall = float(np.count_nonzero(source_edges & morphology.dilation(candidate_edges, radius))) / source_total
    return 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)


def _histogram_similarity(source: Any, candidate: Any) -> float:
    np, _, _ = _imports()
    values = []
    for channel in range(3):
        left, _ = np.histogram(source[..., channel], bins=32, range=(0, 256))
        right, _ = np.histogram(candidate[..., channel], bins=32, range=(0, 256))
        left = left.astype(float) / max(float(left.sum()), 1.0)
        right = right.astype(float) / max(float(right.sum()), 1.0)
        values.append(float(np.sum(np.sqrt(left * right))))
    return sum(values) / len(values)


def compare_images(
    source_path: Path,
    candidate_path: Path,
    heatmap_path: Path,
    *,
    ignore_regions: list[dict[str, int]] | None = None,
) -> dict[str, float]:
    np, Image, libraries = _imports()
    _, _, sk_metrics, _ = libraries
    source = _load_rgb(source_path)
    candidate = _load_rgb(candidate_path)
    if source.shape != candidate.shape:
        raise ValueError(
            f"screenshot dimensions differ: source={source.shape}, candidate={candidate.shape}"
        )
    source, candidate = _apply_regions(source, candidate, ignore_regions or [])
    ssim = float(sk_metrics.structural_similarity(source, candidate, channel_axis=2, data_range=255))
    difference = np.abs(source.astype(np.int16) - candidate.astype(np.int16)).astype(np.uint8)
    intensity = difference.max(axis=2).astype(np.float32) / 255.0
    heat = np.zeros_like(source)
    heat[..., 0] = np.clip(intensity * 255 * 1.5, 0, 255).astype(np.uint8)
    heat[..., 1] = np.clip((intensity - 0.35) * 255 * 1.5, 0, 255).astype(np.uint8)
    rendered = np.maximum((source.astype(np.float32) * 0.25).astype(np.uint8), heat)
    heatmap_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rendered, mode="RGB").save(heatmap_path, "WEBP", lossless=True, method=6)
    return {
        "ssim": round(ssim, 4),
        "edge_f1": round(_edge_f1(source, candidate), 4),
        "color_histogram": round(_histogram_similarity(source, candidate), 4),
        "normalized_mae": round(float(difference.mean() / 255.0), 4),
    }


def compare_stability(first_path: Path, second_path: Path) -> dict[str, Any]:
    np, _, _ = _imports()
    first, second = _load_rgb(first_path), _load_rgb(second_path)
    if first.shape != second.shape:
        raise ValueError("stability frame dimensions differ")
    difference = np.abs(first.astype(np.int16) - second.astype(np.int16))
    normalized_mae = float(difference.mean() / 255.0)
    ratio = float(np.count_nonzero(difference.max(axis=2) > STABILITY_PIXEL_DELTA) / (first.shape[0] * first.shape[1]))
    return {
        "stable": normalized_mae <= STABILITY_NMAE_THRESHOLD and ratio <= STABILITY_CHANGED_PIXEL_THRESHOLD,
        "normalized_mae": round(normalized_mae, 6),
        "changed_pixel_ratio": round(ratio, 6),
        "nmae_threshold": STABILITY_NMAE_THRESHOLD,
        "changed_pixel_threshold": STABILITY_CHANGED_PIXEL_THRESHOLD,
        "pixel_delta": STABILITY_PIXEL_DELTA,
    }


def analyze_visual_content(path: Path) -> dict[str, Any]:
    np, _, _ = _imports()
    image = _load_rgb(path)
    background = np.median(image.reshape(-1, 3), axis=0)
    difference = np.abs(image.astype(np.int16) - background.astype(np.int16))
    ratio = float(np.count_nonzero(difference.max(axis=2) > VISUAL_CONTENT_PIXEL_DELTA) / (image.shape[0] * image.shape[1]))
    channel_std = np.std(image.astype(np.float32), axis=(0, 1))
    rgb_std = float(channel_std.max())
    return {
        "near_uniform": ratio <= VISUAL_CONTENT_CHANGED_PIXEL_MIN and rgb_std <= VISUAL_CONTENT_RGB_STD_MIN,
        "changed_pixel_ratio": round(ratio, 6),
        "rgb_std": round(rgb_std, 4),
        "channel_std": [round(float(value), 4) for value in channel_std],
        "background_rgb": [round(float(value), 1) for value in background],
    }


def convert_capture(
    png_path: Path,
    webp_path: Path,
    thumbnail_path: Path,
    *,
    thumbnail_width: int = 420,
) -> dict[str, Any]:
    _, Image, _ = _imports()
    webp_path.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(png_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        rgb.save(webp_path, "WEBP", lossless=True, method=6)
        thumb_height = max(1, round(height * thumbnail_width / width))
        rgb.resize((thumbnail_width, thumb_height), Image.Resampling.LANCZOS).save(
            thumbnail_path, "WEBP", quality=78, method=6
        )
    return {
        "width": width,
        "height": height,
        "bytes": webp_path.stat().st_size,
        "sha256": hashlib.sha256(webp_path.read_bytes()).hexdigest(),
    }
