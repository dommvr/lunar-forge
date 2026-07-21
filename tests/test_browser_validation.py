import json
import tomllib
from pathlib import Path

import pytest

import lunar_forge.workflows.browser_validation as browser_module
from lunar_forge.permissions import PermissionLevel, PermissionRequest
from lunar_forge.tools.registry import create_tool_registry
from lunar_forge.workflows.browser_validation import (
    MAX_LOG_ENTRIES,
    MAX_LOG_TEXT_CHARACTERS,
    MAX_SCREENSHOT_BYTES,
    VIEWPORT,
    run_browser_validation,
)


class FakeMessage:
    def __init__(self, message_type, text):
        self.type = message_type
        self.text = text


class FakeRequest:
    def __init__(self, url, failure=None):
        self.url = url
        self.failure = failure


class FakeRoute:
    def __init__(self, url):
        self.request = FakeRequest(url)
        self.continued = False
        self.aborted_with = None

    def continue_(self):
        self.continued = True

    def abort(self, reason):
        self.aborted_with = reason


class FakeLocator:
    def __init__(self, count):
        self._count = count

    def count(self):
        return self._count


class FakePage:
    def __init__(
        self,
        *,
        title="Local App",
        final_url="http://127.0.0.1:8000/dashboard",
        selectors=None,
        console_messages=None,
        failed_requests=None,
        routed_urls=None,
        screenshot_bytes=b"fake-png",
    ):
        self._title = title
        self.url = final_url
        self.selectors = selectors or {}
        self.console_messages = console_messages or []
        self.failed_request_events = failed_requests or []
        self.routed_urls = routed_urls or []
        self.screenshot_bytes = screenshot_bytes
        self.handlers = {}
        self.route_handler = None
        self.goto_calls = []
        self.screenshot_calls = []
        self.routes = []

    def on(self, event, handler):
        self.handlers[event] = handler

    def route(self, pattern, handler):
        assert pattern == "**/*"
        self.route_handler = handler

    def goto(self, url, *, wait_until, timeout):
        self.goto_calls.append((url, wait_until, timeout))
        for message in self.console_messages:
            self.handlers["console"](message)
        for request in self.failed_request_events:
            self.handlers["requestfailed"](request)
        for routed_url in self.routed_urls:
            route = FakeRoute(routed_url)
            self.routes.append(route)
            self.route_handler(route)

    def title(self):
        return self._title

    def locator(self, selector):
        return FakeLocator(self.selectors.get(selector, 0))

    def screenshot(self, *, path, full_page):
        self.screenshot_calls.append(
            {"path": path, "full_page": full_page}
        )
        Path(path).write_bytes(self.screenshot_bytes)


class FakeBrowser:
    def __init__(self, page):
        self.page = page
        self.viewport = None
        self.closed = False

    def new_page(self, *, viewport):
        self.viewport = viewport
        return self.page

    def close(self):
        self.closed = True


class FakeChromium:
    def __init__(self, browser):
        self.browser = browser
        self.headless = None

    def launch(self, *, headless):
        self.headless = headless
        return self.browser


class FakePlaywright:
    def __init__(self, page):
        self.browser = FakeBrowser(page)
        self.chromium = FakeChromium(self.browser)
        self.exited = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.exited = True


def _factory_for(page):
    playwright = FakePlaywright(page)
    return playwright, lambda: playwright


def test_browser_extra_is_optional_and_declares_playwright():
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))

    dependencies = pyproject["project"]["dependencies"]
    browser_extra = pyproject["project"]["optional-dependencies"]["browser"]

    assert not any(dependency.startswith("playwright") for dependency in dependencies)
    assert any(dependency.startswith("playwright") for dependency in browser_extra)


def test_unavailable_playwright_returns_clear_error_without_writing(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(browser_module, "_load_playwright_factory", lambda: None)

    result = run_browser_validation(
        "http://127.0.0.1:8000",
        project_root=tmp_path,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert "Playwright is unavailable" in result["error"]
    assert "playwright install chromium" in result["error"]
    assert not (tmp_path / ".agent").exists()


def test_missing_chromium_returns_actionable_install_command(tmp_path):
    def missing_browser_factory():
        raise RuntimeError("Executable doesn't exist at the configured path")

    result = run_browser_validation(
        "http://127.0.0.1:8000",
        project_root=tmp_path,
        _playwright_factory=missing_browser_factory,
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["error"] == (
        "Playwright's Chromium browser is unavailable. Run "
        "'python -m playwright install chromium'."
    )


@pytest.mark.parametrize(
    "url",
    (
        "https://example.com",
        "http://localhost.example.com:8000",
        "file:///tmp/index.html",
        "http://user:password@localhost:8000",
    ),
)
def test_browser_validation_rejects_non_local_or_credentialed_urls(url, tmp_path):
    def unexpected_factory():
        raise AssertionError("Invalid URL must be rejected before Playwright starts")

    result = run_browser_validation(
        url,
        project_root=tmp_path,
        _playwright_factory=unexpected_factory,
    )

    assert result["ok"] is False
    assert result["screenshot_path"] is None


def test_browser_validation_captures_page_data_and_local_screenshot(tmp_path):
    page = FakePage(
        selectors={"h1": 1, "[data-ready]": 2},
        console_messages=[
            FakeMessage("log", "ignored"),
            FakeMessage("error", "render failed"),
        ],
        failed_requests=[
            FakeRequest("http://localhost:8000/api/data", "net::ERR_FAILED")
        ],
        routed_urls=[
            "http://127.0.0.1:8000/app.js",
            "https://cdn.example.com/library.js",
            "data:text/plain,local",
        ],
    )
    playwright, factory = _factory_for(page)

    result = run_browser_validation(
        "http://127.0.0.1:8000",
        checks=["h1", "[data-ready]"],
        project_root=tmp_path,
        _playwright_factory=factory,
    )

    assert result["ok"] is True
    assert result["status"] == "passed"
    assert result["title"] == "Local App"
    assert result["final_url"] == "http://127.0.0.1:8000/dashboard"
    assert result["console_errors"] == ["render failed"]
    assert result["failed_requests"] == [
        {
            "url": "http://localhost:8000/api/data",
            "error": "net::ERR_FAILED",
        },
        {
            "url": "https://cdn.example.com/library.js",
            "error": "Blocked non-local browser request.",
        },
    ]
    assert result["checks"] == [
        {"selector": "h1", "passed": True},
        {"selector": "[data-ready]", "passed": True},
    ]
    screenshot_path = result["screenshot_path"]
    assert screenshot_path.startswith(".agent/artifacts/browser/browser-")
    assert screenshot_path.endswith(".png")
    artifact_path = (tmp_path / screenshot_path).resolve()
    artifact_path.relative_to(tmp_path.resolve())
    assert artifact_path.read_bytes() == b"fake-png"
    assert page.screenshot_calls == [
        {"path": str(artifact_path), "full_page": False}
    ]
    assert page.goto_calls == [
        ("http://127.0.0.1:8000", "domcontentloaded", 30_000)
    ]
    assert page.routes[0].continued is True
    assert page.routes[1].aborted_with == "blockedbyclient"
    assert page.routes[2].continued is True
    assert playwright.browser.viewport == VIEWPORT
    assert playwright.browser.closed is True
    assert playwright.exited is True
    json.dumps(result)


def test_full_page_screenshot_uses_requested_viewport_and_stays_confined(
    tmp_path,
):
    page = FakePage()
    playwright, factory = _factory_for(page)

    result = run_browser_validation(
        "http://localhost:5173",
        full_page=True,
        width=1440,
        height=1200,
        project_root=tmp_path,
        _playwright_factory=factory,
    )

    artifact_path = (tmp_path / result["screenshot_path"]).resolve()
    artifact_path.relative_to(tmp_path.resolve())
    assert result["ok"] is True
    assert artifact_path.is_file()
    assert playwright.browser.viewport == {"width": 1440, "height": 1200}
    assert page.screenshot_calls == [
        {"path": str(artifact_path), "full_page": True}
    ]


def test_browser_tool_is_registered_in_normal_tool_schemas(tmp_path):
    registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=lambda request: False,
    )

    schema_names = {
        schema["function"]["name"] for schema in registry.schemas()
    }

    assert "run_browser_validation" in registry.names()
    assert "run_browser_validation" in schema_names


def test_screenshot_can_be_disabled_without_creating_artifact_directory(tmp_path):
    page = FakePage()
    _, factory = _factory_for(page)

    result = run_browser_validation(
        "http://localhost:3000",
        screenshot=False,
        project_root=tmp_path,
        _playwright_factory=factory,
    )

    assert result["ok"] is True
    assert result["screenshot_path"] is None
    assert page.screenshot_calls == []
    assert not (tmp_path / ".agent").exists()


def test_browser_output_redacts_secrets_from_urls_and_logs(tmp_path):
    secret = "do-not-log-this"
    page = FakePage(
        final_url=f"http://localhost:3000/callback?token={secret}&view=ready#fragment",
        console_messages=[FakeMessage("error", f"api_key={secret}")],
        failed_requests=[
            FakeRequest(
                f"http://localhost:3000/api?access_token={secret}",
                f"password: {secret}",
            )
        ],
    )
    _, factory = _factory_for(page)

    result = run_browser_validation(
        f"http://localhost:3000/start?token={secret}",
        screenshot=False,
        project_root=tmp_path,
        _playwright_factory=factory,
    )

    serialized = json.dumps(result)
    assert result["ok"] is True
    assert secret not in serialized
    assert "REDACTED" in serialized
    assert "fragment" not in result["final_url"]


def test_oversized_screenshot_is_removed_and_reported(tmp_path):
    page = FakePage(screenshot_bytes=b"x" * (MAX_SCREENSHOT_BYTES + 1))
    _, factory = _factory_for(page)

    result = run_browser_validation(
        "http://localhost:8000",
        project_root=tmp_path,
        _playwright_factory=factory,
    )

    assert result["ok"] is False
    assert "screenshot exceeded the size limit" in result["error"]
    assert list((tmp_path / ".agent/artifacts/browser").glob("*.png")) == []


def test_missing_selector_is_a_clear_validation_failure(tmp_path):
    page = FakePage(selectors={"h1": 1})
    _, factory = _factory_for(page)

    result = run_browser_validation(
        "http://[::1]:8000",
        screenshot=False,
        checks=["h1", "#missing"],
        project_root=tmp_path,
        _playwright_factory=factory,
    )

    assert result["ok"] is False
    assert result["error"] == "One or more browser checks failed."
    assert result["checks"][-1] == {"selector": "#missing", "passed": False}


def test_browser_logs_are_bounded_and_marked_truncated(tmp_path):
    page = FakePage(
        console_messages=[
            FakeMessage("error", "x" * 1_000)
            for _ in range(MAX_LOG_ENTRIES + 5)
        ],
        failed_requests=[
            FakeRequest(
                f"http://localhost:8000/failure/{index}",
                "y" * 1_000,
            )
            for index in range(MAX_LOG_ENTRIES + 5)
        ],
    )
    _, factory = _factory_for(page)

    result = run_browser_validation(
        "http://localhost:8000",
        screenshot=False,
        project_root=tmp_path,
        _playwright_factory=factory,
    )

    assert result["ok"] is True
    assert result["truncated"] is True
    assert len(result["console_errors"]) == MAX_LOG_ENTRIES
    assert len(result["failed_requests"]) == MAX_LOG_ENTRIES
    assert all(
        len(message) <= MAX_LOG_TEXT_CHARACTERS
        for message in result["console_errors"]
    )
    assert all(
        len(request["error"]) <= MAX_LOG_TEXT_CHARACTERS
        for request in result["failed_requests"]
    )


def test_browser_tool_is_permission_gated_and_hidden_in_plan_mode(
    monkeypatch,
    tmp_path,
):
    plan_registry = create_tool_registry(tmp_path, mode="plan")
    requests: list[PermissionRequest] = []
    calls = []

    def fake_validation(url, screenshot=True, checks=None, *, project_root="."):
        calls.append((url, screenshot, checks, project_root))
        return {
            "ok": True,
            "title": "Mock",
            "final_url": url,
            "console_errors": [],
            "failed_requests": [],
            "screenshot_path": None,
            "checks": [],
            "truncated": False,
        }

    monkeypatch.setattr(browser_module, "run_browser_validation", fake_validation)
    denied_registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=lambda request: requests.append(request) or False,
    )

    assert "run_browser_validation" not in plan_registry.names()
    assert "run_browser_validation" in denied_registry.names()
    assert denied_registry.get("run_browser_validation").permission is (
        PermissionLevel.EXECUTE
    )

    denied = denied_registry.execute(
        "run_browser_validation",
        {"url": "http://localhost:8000"},
    )

    assert denied["permission_denied"] is True
    assert calls == []
    assert requests[0].tool_name == "run_browser_validation"

    approved_registry = create_tool_registry(
        tmp_path,
        mode="default",
        approval_callback=lambda request: True,
    )
    approved = approved_registry.execute(
        "run_browser_validation",
        {
            "url": "http://localhost:8000",
            "screenshot": False,
            "checks": ["main"],
        },
    )

    assert approved["ok"] is True
    assert calls == [
        ("http://localhost:8000", False, ["main"], tmp_path)
    ]
