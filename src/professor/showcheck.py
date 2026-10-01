"""Checking that a slide can be shown, before any listener sees it.

A slide's diagram and equation are drawn in a headless browser with the same
renderers the presentation uses, so a pass here means the page will draw them.
Where no browser can be found the check can't be made, and that is reported as
its own outcome: never as a pass.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

PAGE = Path(__file__).resolve().parent / "page" / "check.html"
BROWSERS = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
RESULT = re.compile(r"PROFESSOR-CHECK (\{.*?\})\s*</pre>", re.S)


@dataclass(frozen=True)
class Check:
    checked: bool            # was a check actually made?
    error: str | None = None  # why the slide can't be shown, if it can't

    @property
    def ok(self) -> bool:
        return self.checked and self.error is None


def browser() -> str | None:
    """The browser to check with: PROFESSOR_BROWSER, or the first one found."""
    if named := os.environ.get("PROFESSOR_BROWSER"):
        return shutil.which(named)
    return next((path for name in BROWSERS if (path := shutil.which(name))), None)


def check(diagram: str = "", math: str = "", *, timeout_s: float = 30) -> Check:
    """Can this diagram and this equation be drawn?"""
    if not diagram and not math:
        return Check(checked=True)  # nothing that could fail to draw
    exe = browser()
    if exe is None:
        return Check(checked=False, error="no browser was found to draw it with (set PROFESSOR_BROWSER)")
    payload = base64.b64encode(json.dumps({"diagram": diagram, "math": math}).encode()).decode()
    with tempfile.TemporaryDirectory(prefix="professor-check-") as profile:
        try:
            proc = subprocess.run(
                [exe, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}",
                 "--allow-file-access-from-files", "--virtual-time-budget=15000", "--dump-dom",
                 f"{PAGE.as_uri()}#{payload}"],
                capture_output=True, text=True, timeout=timeout_s)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return Check(checked=False, error=f"the browser couldn't be run ({type(exc).__name__})")
    found = RESULT.search(proc.stdout)
    if not found:
        return Check(checked=False, error="the browser gave no result")
    result = json.loads(_unescape(found.group(1)))
    if result.get("fault"):
        return Check(checked=False, error=f"the check itself failed: {result['fault']}")
    problems = [f"The {what} couldn't be drawn: {result[what]}" for what in ("diagram", "math")
                if result.get(what)]
    return Check(checked=True, error="\n".join(problems).replace("The math", "The equation") or None)


def _unescape(text: str) -> str:
    """--dump-dom prints the DOM as HTML, so the text comes back escaped."""
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")
