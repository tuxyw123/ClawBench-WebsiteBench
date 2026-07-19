"""Deterministic visual, text, and geometry similarity metrics."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity


def _rgb(value: Path | str | Image.Image | np.ndarray) -> np.ndarray:
    if isinstance(value, (str, Path)):
        image = Image.open(value).convert("RGB")
        return np.asarray(image, dtype=np.uint8)
    if isinstance(value, Image.Image):
        return np.asarray(value.convert("RGB"), dtype=np.uint8)
    array = np.asarray(value)
    if array.ndim == 2:
        array = np.repeat(array[:, :, None], 3, axis=2)
    if array.shape[2] == 4:
        array = array[:, :, :3]
    return array.astype(np.uint8)


def _aligned(reference: np.ndarray, candidate: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if reference.shape == candidate.shape:
        return reference, candidate
    height, width = reference.shape[:2]
    resized = Image.fromarray(candidate).resize((width, height), Image.Resampling.LANCZOS)
    return reference, np.asarray(resized, dtype=np.uint8)


def apply_masks(image: np.ndarray, masks: Iterable[tuple[int, int, int, int]]) -> np.ndarray:
    result = image.copy()
    for x, y, width, height in masks:
        result[max(0, y) : max(0, y + height), max(0, x) : max(0, x + width)] = 127
    return result


def ssim_score(reference: np.ndarray, candidate: np.ndarray) -> float:
    reference, candidate = _aligned(_rgb(reference), _rgb(candidate))
    score = structural_similarity(reference, candidate, channel_axis=2, data_range=255)
    return max(0.0, min(1.0, float(score)))


def _edges(image: np.ndarray) -> np.ndarray:
    gray = _rgb(image).astype(np.float32).mean(axis=2)
    horizontal = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    vertical = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    magnitude = np.hypot(horizontal, vertical)
    threshold = max(18.0, float(np.percentile(magnitude, 78)))
    return magnitude >= threshold


def edge_f1_score(reference: np.ndarray, candidate: np.ndarray) -> float:
    reference, candidate = _aligned(_rgb(reference), _rgb(candidate))
    expected = _edges(reference)
    actual = _edges(candidate)
    true_positive = int(np.logical_and(expected, actual).sum())
    false_positive = int(np.logical_and(~expected, actual).sum())
    false_negative = int(np.logical_and(expected, ~actual).sum())
    denominator = 2 * true_positive + false_positive + false_negative
    return 1.0 if denominator == 0 else (2 * true_positive) / denominator


def histogram_score(reference: np.ndarray, candidate: np.ndarray) -> float:
    reference, candidate = _aligned(_rgb(reference), _rgb(candidate))
    scores = []
    for channel in range(3):
        expected, _ = np.histogram(reference[:, :, channel], bins=32, range=(0, 256), density=True)
        actual, _ = np.histogram(candidate[:, :, channel], bins=32, range=(0, 256), density=True)
        denominator = math.sqrt(float(np.dot(expected, expected) * np.dot(actual, actual)))
        scores.append(float(np.dot(expected, actual)) / denominator if denominator else 1.0)
    return max(0.0, min(1.0, sum(scores) / len(scores)))


def _tokens(text: str) -> Counter[str]:
    return Counter(re.findall(r"[\w$%.+-]+", text.casefold()))


def text_f1_score(reference: str, candidate: str) -> float:
    expected = _tokens(reference)
    actual = _tokens(candidate)
    common = sum((expected & actual).values())
    total = sum(expected.values()) + sum(actual.values())
    return 1.0 if total == 0 else 2 * common / total


def _iou(first: dict[str, float], second: dict[str, float]) -> float:
    box_keys = ("x", "y", "width", "height")
    if all(first[key] == second[key] for key in box_keys):
        return 1.0
    left = max(first["x"], second["x"])
    top = max(first["y"], second["y"])
    right = min(first["x"] + first["width"], second["x"] + second["width"])
    bottom = min(first["y"] + first["height"], second["y"] + second["height"])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    union = first["width"] * first["height"] + second["width"] * second["height"] - intersection
    return intersection / union if union else 1.0


def geometry_score(
    reference: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> float:
    """Match visible elements by semantic role/name, then average box IoU.

    Each item is ``{"role": str, "name": str, "x": ..., "y": ...,
    "width": ..., "height": ...}`` in normalized viewport coordinates.
    """

    if not reference and not candidate:
        return 1.0
    candidate_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in candidate:
        key = (str(item.get("role", "")), str(item.get("name", "")).casefold().strip())
        candidate_by_key.setdefault(key, []).append(item)
    scores = []
    for expected in reference:
        key = (str(expected.get("role", "")), str(expected.get("name", "")).casefold().strip())
        choices = candidate_by_key.get(key, [])
        if not choices:
            scores.append(0.0)
            continue
        best = max(choices, key=lambda item: _iou(expected, item))
        scores.append(_iou(expected, best))
        choices.remove(best)
    unmatched = sum(len(items) for items in candidate_by_key.values())
    scores.extend([0.0] * unmatched)
    return sum(scores) / len(scores) if scores else 0.0


def checkpoint_similarity(
    reference_image: np.ndarray,
    candidate_image: np.ndarray,
    *,
    reference_text: str,
    candidate_text: str,
    reference_geometry: list[dict[str, Any]],
    candidate_geometry: list[dict[str, Any]],
    masks: Iterable[tuple[int, int, int, int]] = (),
) -> dict[str, float]:
    reference, candidate = _aligned(_rgb(reference_image), _rgb(candidate_image))
    mask_list = list(masks)
    reference = apply_masks(reference, mask_list)
    candidate = apply_masks(candidate, mask_list)
    metrics = {
        "ssim": ssim_score(reference, candidate),
        "edge_f1": edge_f1_score(reference, candidate),
        "color_histogram": histogram_score(reference, candidate),
        "text": text_f1_score(reference_text, candidate_text),
        "geometry": geometry_score(reference_geometry, candidate_geometry),
    }
    metrics["similarity"] = (
        metrics["ssim"] * 0.30
        + metrics["edge_f1"] * 0.20
        + metrics["color_histogram"] * 0.15
        + metrics["text"] * 0.20
        + metrics["geometry"] * 0.15
    )
    return metrics
