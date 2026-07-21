"""Optional, bounded browser validation for an already-running local site."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from lunar_forge.tools.files import safe_path


ARTIFACT_DIRECTORY = ".agent/artifacts/browser"
NAVIGATION_TIMEOUT_MS = 30_000
MAX_URL_CHARACTERS = 2_000
MAX_LOG_TEXT_CHARACTERS = 500
MAX_LOG_ENTRIES = 10
MAX_CHECKS = 20
MAX_CHECK_CHARACTERS = 500
MAX_SCREENSHOT_BYTES = 5_000_000
VIEWPORT = MappingProxyType({"width": 1280, "height": 720})
_SENSITIVE_URL_KEY_PARTS = (
    "apikey",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
)
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)((?:api[_-]?key|authorization|credential|password|secret|token)"
    r"\s*[=:]\s*)([^\s&,;]+)"
)

PlaywrightFactory = Callable[[], Any]


def run_browser_validation(
    url: str,
    screenshot: bool = True,
    checks: Sequence[str] | None = None,
    *,
    project_root: str | Path = ".",
    _playwright_factory: PlaywrightFactory | None = None,
) -> dict[str, Any]:
    """Inspect an existing loopback URL without starting a development server.

    "checks" is an optional list of CSS selectors expected to match at least
    one element. Playwright is imported only when this function is called.
    """
    try:
        local_url = _validated_local_url(url)
        normalized_checks = _validated_checks(checks)
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Project root must be an existing directory.")
    except (TypeError, ValueError) as exc:
        return _error_result(str(exc))

    factory = _playwright_factory or _load_playwright_factory()
    if factory is None:
        return _error_result(
            "Playwright is unavailable. Install lunar-forge[browser], then run "
            "'python -m playwright install chromium'."
        )

    console_errors: list[str] = []
    failed_requests: list[dict[str, str]] = []
    check_results: list[dict[str, Any]] = []
    state = {"truncated": False}
    title = ""
    final_url = _redacted_url(local_url)
    screenshot_path: str | None = None
    screenshot_file: Path | None = None

    try:
        with factory() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport=dict(VIEWPORT))
                page.on(
                    "console",
                    lambda message: _capture_console_error(
                        message,
                        console_errors,
                        state,
                    ),
                )
                page.on(
                    "requestfailed",
                    lambda request: _capture_failed_request(
                        request,
                        failed_requests,
                        state,
                    ),
                )
                page.route(
                    "**/*",
                    lambda route: _route_local_request(
                        route,
                        failed_requests,
                        state,
                    ),
                )
                page.goto(
                    local_url,
                    wait_until="domcontentloaded",
                    timeout=NAVIGATION_TIMEOUT_MS,
                )
                title, title_truncated = _bounded_text(
                    _event_value(page, "title"),
                    MAX_LOG_TEXT_CHARACTERS,
                )
                state["truncated"] = state["truncated"] or title_truncated
                actual_final_url = str(_event_value(page, "url"))
                _validated_local_url(actual_final_url)
                final_url, url_truncated = _bounded_text(
                    _redacted_url(actual_final_url),
                    MAX_URL_CHARACTERS,
                )
                state["truncated"] = state["truncated"] or url_truncated

                for selector in normalized_checks:
                    check_results.append(_run_selector_check(page, selector))

                if screenshot:
                    screenshot_file = _screenshot_file(root)
                    screenshot_file.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(screenshot_file), full_page=False)
                    if not screenshot_file.is_file():
                        raise RuntimeError("Playwright did not create the screenshot.")
                    if screenshot_file.stat().st_size > MAX_SCREENSHOT_BYTES:
                        screenshot_file.unlink(missing_ok=True)
                        raise RuntimeError("Browser screenshot exceeded the size limit.")
                    screenshot_path = screenshot_file.relative_to(root).as_posix()
            finally:
                browser.close()
    except Exception as exc:
        if screenshot_file is not None and screenshot_path is None:
            try:
                screenshot_file.unlink(missing_ok=True)
            except OSError:
                pass
        result = _result(
            ok=False,
            title=title,
            final_url=final_url,
            console_errors=console_errors,
            failed_requests=failed_requests,
            screenshot_path=screenshot_path,
            checks=check_results,
            truncated=state["truncated"],
        )
        result["error"] = _safe_exception_message(exc)
        return result

    checks_passed = all(check["passed"] for check in check_results)
    result = _result(
        ok=checks_passed,
        title=title,
        final_url=final_url,
        console_errors=console_errors,
        failed_requests=failed_requests,
        screenshot_path=screenshot_path,
        checks=check_results,
        truncated=state["truncated"],
    )
    if not checks_passed:
        result["error"] = "One or more browser checks failed."
    return result


def _load_playwright_factory() -> PlaywrightFactory | None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    return sync_playwright


def _validated_local_url(url: str) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Browser validation URL must be a non-empty string.")
    normalized = url.strip()
    if len(normalized) > MAX_URL_CHARACTERS:
        raise ValueError("Browser validation URL is too long.")
    try:
        parsed = urlsplit(normalized)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Browser validation URL is invalid.") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Browser validation URL must use http or https.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Browser validation URL must not contain credentials.")
    if not parsed.hostname:
        raise ValueError("Browser validation URL requires a host.")
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("Browser validation URL has an invalid port.")

    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost":
        return normalized
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError as exc:
        raise ValueError("Browser validation is limited to local loopback URLs.") from exc
    if not address.is_loopback:
        raise ValueError("Browser validation is limited to local loopback URLs.")
    return normalized


def _validated_checks(checks: Sequence[str] | None) -> tuple[str, ...]:
    if checks is None:
        return ()
    if isinstance(checks, (str, bytes)) or not isinstance(checks, Sequence):
        raise TypeError("Browser checks must be a list of CSS selectors.")
    if len(checks) > MAX_CHECKS:
        raise ValueError(f"Browser validation supports at most {MAX_CHECKS} checks.")
    normalized: list[str] = []
    for check in checks:
        if not isinstance(check, str) or not check.strip():
            raise ValueError("Each browser check must be a non-empty CSS selector.")
        selector = check.strip()
        if len(selector) > MAX_CHECK_CHARACTERS:
            raise ValueError("Browser check selector is too long.")
        normalized.append(selector)
    return tuple(normalized)


def _route_local_request(
    route: Any,
    failed_requests: list[dict[str, str]],
    state: dict[str, bool],
) -> None:
    request_url = str(_event_value(route.request, "url"))
    if _is_allowed_page_url(request_url):
        route.continue_()
        return
    _append_failed_request(
        failed_requests,
        request_url,
        "Blocked non-local browser request.",
        state,
    )
    route.abort("blockedbyclient")


def _is_allowed_page_url(url: str) -> bool:
    scheme = urlsplit(url).scheme.lower()
    if scheme in {"about", "blob", "data"}:
        return True
    try:
        _validated_local_url(url)
    except ValueError:
        return False
    return True


def _capture_console_error(
    message: Any,
    console_errors: list[str],
    state: dict[str, bool],
) -> None:
    if str(_event_value(message, "type")).lower() != "error":
        return
    text, truncated = _bounded_text(
        _redacted_text(_event_value(message, "text")),
        MAX_LOG_TEXT_CHARACTERS,
    )
    state["truncated"] = state["truncated"] or truncated
    if len(console_errors) >= MAX_LOG_ENTRIES:
        state["truncated"] = True
        return
    console_errors.append(text)


def _capture_failed_request(
    request: Any,
    failed_requests: list[dict[str, str]],
    state: dict[str, bool],
) -> None:
    failure = _event_value(request, "failure") or "Request failed."
    _append_failed_request(
        failed_requests,
        str(_event_value(request, "url")),
        str(failure),
        state,
    )


def _append_failed_request(
    failed_requests: list[dict[str, str]],
    url: str,
    error: str,
    state: dict[str, bool],
) -> None:
    if len(failed_requests) >= MAX_LOG_ENTRIES:
        state["truncated"] = True
        return
    bounded_url, url_truncated = _bounded_text(
        _redacted_url(url),
        MAX_URL_CHARACTERS,
    )
    bounded_error, error_truncated = _bounded_text(
        _redacted_text(error),
        MAX_LOG_TEXT_CHARACTERS,
    )
    state["truncated"] = (
        state["truncated"] or url_truncated or error_truncated
    )
    failed_requests.append({"url": bounded_url, "error": bounded_error})


def _run_selector_check(page: Any, selector: str) -> dict[str, Any]:
    try:
        passed = page.locator(selector).count() > 0
    except Exception as exc:
        return {
            "selector": selector,
            "passed": False,
            "error": _safe_exception_message(exc),
        }
    return {"selector": selector, "passed": passed}


def _screenshot_file(root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    suffix = uuid4().hex[:8]
    return safe_path(
        root,
        f"{ARTIFACT_DIRECTORY}/browser-{timestamp}-{suffix}.png",
    )


def _event_value(event: Any, name: str) -> Any:
    value = getattr(event, name, "")
    return value() if callable(value) else value


def _bounded_text(value: Any, limit: int) -> tuple[str, bool]:
    text = str(value)
    if len(text) <= limit:
        return text, False
    return text[:limit], True


def _safe_exception_message(exc: Exception) -> str:
    redacted = _redacted_text(exc)
    message, _ = _bounded_text(redacted, MAX_LOG_TEXT_CHARACTERS)
    return f"Browser validation failed with {type(exc).__name__}: {message}"


def _redacted_text(value: Any) -> str:
    return _SENSITIVE_ASSIGNMENT_PATTERN.sub(r"\1[REDACTED]", str(value))


def _redacted_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    redacted_query = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        normalized_key = "".join(
            character for character in key.casefold() if character.isalnum()
        )
        redacted_value = (
            "[REDACTED]"
            if any(part in normalized_key for part in _SENSITIVE_URL_KEY_PARTS)
            else value
        )
        redacted_query.append((key, redacted_value))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(redacted_query),
            "",
        )
    )


def _error_result(error: str) -> dict[str, Any]:
    bounded_error, truncated = _bounded_text(error, MAX_LOG_TEXT_CHARACTERS)
    result = _result(
        ok=False,
        title="",
        final_url="",
        console_errors=[],
        failed_requests=[],
        screenshot_path=None,
        checks=[],
        truncated=truncated,
    )
    result["error"] = bounded_error
    return result


def _result(
    *,
    ok: bool,
    title: str,
    final_url: str,
    console_errors: list[str],
    failed_requests: list[dict[str, str]],
    screenshot_path: str | None,
    checks: list[dict[str, Any]],
    truncated: bool,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "title": title,
        "final_url": final_url,
        "console_errors": console_errors,
        "failed_requests": failed_requests,
        "screenshot_path": screenshot_path,
        "checks": checks,
        "truncated": truncated,
    }
