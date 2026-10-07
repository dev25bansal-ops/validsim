"""Boot the real app and prove the new UI states actually render, via Playwright.

Two scenarios, both against a live server:
  1. no API key  -> the dashboard loads and a BLOCK verdict shows its reasons;
  2. key set -> the dashboard body is hidden and the auth panel is shown.

Runs only when Playwright and its Chromium build are installed; otherwise it
skips, so it never becomes a CI dependency.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")

from playwright.sync_api import Error as PlaywrightError  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _screenshot(name: str) -> Path:
    """Screenshot path outside the repo, so a test run leaves no artefacts."""
    return Path(tempfile.gettempdir()) / f"validsim-web-{name}"


def _fetch_run(port: int, api_key: str | None, run_id: str) -> dict:
    """GET a stored run's scorecard, exactly as the dashboard does."""
    import json

    request = urllib.request.Request(  # noqa: S310
        f"http://127.0.0.1:{port}/api/v1/validations/{run_id}/scorecard"
    )
    if api_key:
        request.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read())


def _serve(port: int, api_key: str | None) -> subprocess.Popen[bytes]:
    import os

    env = dict(os.environ)
    env["VALIDSIM_API_KEY"] = api_key or ""
    env["VALIDSIM_ENV"] = "development"
    return subprocess.Popen(  # noqa: S603
        [
            sys.executable, "-m", "uvicorn", "validsim.api.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning",
        ],
        env=env,
    )


def _wait_for(port: int, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(  # noqa: S310
                f"http://127.0.0.1:{port}/api/v1/health", timeout=2
            ) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


@pytest.fixture(scope="module")
def browser():
    try:
        with sync_playwright() as playwright:
            try:
                instance = playwright.chromium.launch()
            except PlaywrightError as exc:
                pytest.skip(f"chromium not available: {exc}")
            yield instance
            instance.close()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"playwright unavailable: {exc}")


def _seed_blocked_run(port: int, api_key: str | None) -> dict:
    """Create a run that the gate will BLOCK, so the reasons are non-empty."""
    payload = (
        '{"checkpoint_id":"ckpt-browser","threshold":100.0,'
        '"task":{"task_id":"pick-place","robot":{"name":"franka"},'
        '"environment":{"name":"kitchen"},"episodes":60,"adversarial_count":12}}'
    ).encode()
    request = urllib.request.Request(  # noqa: S310
        f"http://127.0.0.1:{port}/api/v1/validations",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if api_key:
        request.add_header("X-API-Key", api_key)
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
        import json

        return json.loads(response.read())


def test_block_verdict_shows_its_reasons_in_a_real_browser(browser) -> None:
    """The headline claim: a BLOCK is now actionable on screen."""
    port = _free_port()
    server = _serve(port, None)
    try:
        assert _wait_for(port), "server did not start"
        card = _seed_blocked_run(port, None)
        assert card["deploy_decision"] == "BLOCK"
        assert card["block_reasons"], "expected a BLOCK with reasons"

        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        page.wait_for_selector("#run-form", state="visible")
        page.click("#run-btn")
        page.wait_for_function(
            "() => !document.getElementById('block-reasons').hidden",
            timeout=60000,
        )
        # The click starts a NEW run, so compare against what the panel
        # actually loaded -- not the run seeded above.
        rendered = page.locator("#block-reasons-list li").all_inner_texts()
        run_id = page.inner_text("#scorecard-run-meta").split("\u00b7")[0].strip()
        stored = _fetch_run(port, None, run_id)
        assert stored["deploy_decision"] == "BLOCK"
        assert rendered == list(stored["block_reasons"]), (
            "every engine block reason must reach the screen, verbatim and in order"
        )
        assert len(rendered) >= 1
        heading = page.inner_text("#block-reasons-count").strip()
        plural = "reason" if len(rendered) == 1 else "reasons"
        assert heading == f"Blocked for {len(rendered)} {plural}"
        assert page.locator("#sc-verdict").inner_text().strip().endswith("BLOCK")
        # The adversarial segment is on screen too.
        assert page.locator("#m-adversarial-count").is_visible()
        assert page.locator("#m-adversarial-rate").is_visible()
        assert page.get_attribute("#run-btn", "aria-busy") == "false"
        page.screenshot(path=str(_screenshot("block-reasons.png")), full_page=True)
        page.close()
    finally:
        server.terminate()
        server.wait(timeout=30)


def test_auth_panel_replaces_the_dashboard_when_a_key_is_required(browser) -> None:
    """With a key configured the body must never render raw JSON."""
    port = _free_port()
    server = _serve(port, "browser-test-key")
    try:
        assert _wait_for(port), "server did not start"
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{port}/static/index.html", wait_until="networkidle")
        page.wait_for_selector("#auth-panel", state="visible", timeout=20000)
        assert page.locator("#app-body").is_hidden()
        assert page.locator("#auth-status").is_visible()
        body = page.inner_text("body")
        assert '"detail"' not in body
        assert '{"detail":' not in body
        page.screenshot(path="auth-panel.png", full_page=True)
        page.close()
    finally:
        server.terminate()
        server.wait(timeout=30)


def test_root_entry_point_degrades_to_the_auth_panel(browser) -> None:
    """The path an operator actually types must reach the key prompt.

    This is the regression test for the shipped defect: the app-level
    ``X-API-Key`` dependency lived in ``application.router.dependencies``, and
    FastAPI merges router-level dependencies into each route when it is
    registered, so ``mount_dashboard``'s ``"/"`` route inherited the gate.
    A browser hitting "/" therefore received a bare ``401 application/json``
    body -- no page, no auth panel, no way to authenticate at all. The
    in-page panel was correct code that could never execute on that path.

    ``mount_dashboard`` now registers "/" with the dependency list temporarily
    emptied (the same technique ``create_app`` uses for the un-gated
    ``/metrics`` mount), so the HTML shell loads and the panel can prompt.

    Asserting only that the panel appears would pass even if the API were
    wide open, so this also pins the other half of the contract: the shell is
    public, the data is not.
    """
    port = _free_port()
    server = _serve(port, "browser-test-key")
    try:
        assert _wait_for(port), "server did not start"

        # 1. "/" serves the application, not a JSON error.
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle")
        page.wait_for_selector("#auth-panel", state="visible", timeout=20000)
        body = page.inner_text("body")
        assert "Missing or invalid API key" not in body
        assert '{"detail":' not in body
        # The operator is told what to do, not merely that something failed.
        assert page.locator("#auth-status").is_visible()
        assert page.locator("#api-key").is_visible()
        assert page.locator("#app-body").is_hidden()
        page.close()

        # 2. Exposing the shell must NOT expose the data. The aggregate view of
        #    past runs is the sensitive surface; it must still demand the key.
        #    The route lives under the dashboard prefix -- requesting a path that
        #    does not exist returns 404, which would pass this for the wrong
        #    reason while leaving the real route unchecked.
        anon = browser.new_page()
        anon.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
        status = anon.evaluate(
            """async () => {
                const r = await fetch('/api/v1/dashboard/summary');
                return r.status;
            }"""
        )
        assert status == 401, f"/api/v1/dashboard/summary must stay gated, got {status}"
        history = anon.evaluate(
            """async () => {
                const r = await fetch('/api/v1/dashboard/history');
                return r.status;
            }"""
        )
        assert history == 401, f"/api/v1/dashboard/history must stay gated, got {history}"
        anon.close()
    finally:
        server.terminate()
        server.wait(timeout=30)


def test_wrong_key_is_rejected_without_leaving_a_stale_credential(browser) -> None:
    """A rejected key must be cleared, or every later request fails silently."""
    port = _free_port()
    server = _serve(port, "browser-test-key")
    try:
        assert _wait_for(port), "server did not start"
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{port}/static/index.html", wait_until="networkidle")
        page.wait_for_selector("#auth-panel", state="visible", timeout=20000)
        page.fill("#api-key", "not-the-key")
        page.click("#auth-btn")
        page.wait_for_function(
            "() => document.getElementById('auth-status')"
            "?.textContent?.includes('rejected')",
            timeout=20000,
        )
        # Still on the auth panel, and the field was cleared.
        assert page.locator("#auth-panel").is_visible()
        assert page.locator("#app-body").is_hidden()
        assert page.input_value("#api-key") == ""
        # And nothing was cached in sessionStorage.
        stored = page.evaluate("() => sessionStorage.getItem('vs-api-key')")
        assert stored in (None, ""), stored
        page.close()
    finally:
        server.terminate()
        server.wait(timeout=30)


def test_body_stays_hidden_until_boot_completes(browser) -> None:
    """A protected deployment must not flash a half-populated dashboard."""
    port = _free_port()
    server = _serve(port, "browser-test-key")
    try:
        assert _wait_for(port), "server did not start"
        page = browser.new_page()
        # Block app.js so boot() never completes: the body must remain hidden.
        page.route("**/static/app.js", lambda route: route.abort())
        page.goto(f"http://127.0.0.1:{port}/static/index.html", wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        assert page.locator("#app-body").is_hidden()
        page.close()
    finally:
        server.terminate()
        server.wait(timeout=30)
