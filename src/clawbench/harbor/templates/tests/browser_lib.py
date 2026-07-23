"""Reusable Playwright primitives for sequential black-box comparisons."""

from __future__ import annotations

import json
import time
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright


@dataclass
class CheckRecorder:
    """Convert candidate assertion failures into exact CTRF nodes."""

    tests: list[dict[str, Any]] = field(default_factory=list)
    hard_failures: list[str] = field(default_factory=list)

    def check(
        self,
        name: str,
        operation: Callable[[], None],
        *,
        partial_score: Callable[[], float] | None = None,
    ) -> None:
        started = time.monotonic()
        status = "passed"
        message: str | None = None
        score = 1.0
        try:
            operation()
        except Exception as exc:  # evaluator owns the exception-to-result boundary
            status = "failed"
            message = f"{type(exc).__name__}: {exc}"
            score = partial_score() if partial_score is not None else 0.0
        entry: dict[str, Any] = {
            "name": name,
            "status": status,
            "duration": round((time.monotonic() - started) * 1000),
            "extra": {"clawbench_score": max(0.0, min(1.0, float(score)))},
        }
        if message:
            entry["message"] = message
        self.tests.append(entry)

    def hard_fail(self, reason: str) -> None:
        if reason not in self.hard_failures:
            self.hard_failures.append(reason)

    def write(self, path: Path) -> None:
        statuses = [test["status"] for test in self.tests]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "results": {
                        "tool": {"name": "clawbench-playwright"},
                        "summary": {
                            "tests": len(self.tests),
                            "passed": statuses.count("passed"),
                            "failed": statuses.count("failed"),
                            "skipped": statuses.count("skipped"),
                        },
                        "tests": self.tests,
                        "extra": {"hard_failures": self.hard_failures},
                    }
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )


class BrowserSession(AbstractContextManager["BrowserSession"]):
    """One fresh browser context for either reference or candidate.

    Do not keep a reference session/service alive while untrusted candidate code
    runs. Capture reference facts first, close its browser and service, and only
    then create a candidate session.
    """

    def __init__(
        self,
        base_url: str,
        artifact_dir: Path,
        *,
        label: str,
        viewport: dict[str, int] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/") + "/"
        self.artifact_dir = artifact_dir
        self.label = label
        self.viewport = viewport or {"width": 1440, "height": 900}
        self._playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.console_errors: list[str] = []
        self.request_failures: list[str] = []

    def __enter__(self) -> "BrowserSession":
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(headless=True)
        self.context = self.browser.new_context(viewport=self.viewport)
        self.context.tracing.start(screenshots=True, snapshots=True)
        self.page = self.context.new_page()
        self.page.on(
            "console",
            lambda message: (
                self.console_errors.append(message.text)
                if message.type == "error"
                else None
            ),
        )
        self.page.on(
            "requestfailed",
            lambda request: self.request_failures.append(
                f"{request.method} {request.url}: {request.failure}"
            ),
        )
        return self

    def goto(self, path: str = "/") -> Page:
        assert self.page is not None
        self.page.goto(urljoin(self.base_url, path.lstrip("/")))
        self.page.wait_for_load_state("domcontentloaded")
        return self.page

    def screenshot(self, checkpoint: str, *, full_page: bool = True) -> Path:
        assert self.page is not None
        output = self.artifact_dir / f"{checkpoint}-{self.label}.png"
        self.page.screenshot(path=output, full_page=full_page)
        return output

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.context is not None:
            self.context.tracing.stop(
                path=self.artifact_dir / f"{self.label}-trace.zip"
            )
        if self.browser is not None:
            self.browser.close()
        if self._playwright is not None:
            self._playwright.stop()


def normalized_text(page: Page, locator: str = "body") -> str:
    """Return whitespace-normalized rendered text for behavior assertions."""

    return " ".join(page.locator(locator).inner_text().split())
