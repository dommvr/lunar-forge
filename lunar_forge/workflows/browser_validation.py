"""Optional, bounded browser validation for an already-running local site."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from threading import Thread
from types import MappingProxyType
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from lunar_forge.permissions import (
    ApprovalCallback,
    PermissionLevel,
    PermissionManager,
    dangerous_command_reason,
    normalized_dangerous_command_reason,
)
from lunar_forge.runtime.local_runner import (
    DEFAULT_TIMEOUT_MS,
    executable_path_summary,
    resolve_executable,
    split_command,
)
from lunar_forge.tools.files import safe_path
from lunar_forge.tools.shell import run_command


ARTIFACT_DIRECTORY = ".agent/artifacts/browser"
NAVIGATION_TIMEOUT_MS = 30_000
MAX_URL_CHARACTERS = 2_000
MAX_LOG_TEXT_CHARACTERS = 500
MAX_LOG_ENTRIES = 10
MAX_CHECKS = 20
MAX_CHECK_CHARACTERS = 500
MAX_SCREENSHOT_BYTES = 5_000_000
DEFAULT_VIEWPORT_WIDTH = 1280
DEFAULT_VIEWPORT_HEIGHT = 720
MIN_VIEWPORT_WIDTH = 320
MAX_VIEWPORT_WIDTH = 3840
MIN_VIEWPORT_HEIGHT = 240
MAX_VIEWPORT_HEIGHT = 2160
DEFAULT_SERVER_STARTUP_TIMEOUT_MS = 30_000
MAX_SERVER_STARTUP_TIMEOUT_MS = 300_000
SERVER_STOP_TIMEOUT_SECONDS = 5
SERVER_PROBE_INTERVAL_SECONDS = 0.1
SERVER_PROBE_TIMEOUT_SECONDS = 1.0
MAX_SERVER_OUTPUT_CHARACTERS = 4_000
BROWSER_SETUP_COMMANDS = (
    'python -m pip install -e ".[browser]"',
    "python -m playwright install chromium",
)
VIEWPORT = MappingProxyType(
    {
        "width": DEFAULT_VIEWPORT_WIDTH,
        "height": DEFAULT_VIEWPORT_HEIGHT,
    }
)
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
PopenFactory = Callable[..., Any]
URLProbe = Callable[[str, float], bool]
SetupCommandRunner = Callable[..., dict[str, Any]]
_PLAYWRIGHT_SETUP_ERROR = (
    "Playwright is unavailable. Install browser support with "
    "'python -m pip install -e \".[browser]\"', then run "
    "'python -m playwright install chromium'."
)


def run_browser_setup(
    project_root: str | Path = ".",
    *,
    permission_mode: str = "default",
    runtime_mode: str = "local",
    approval_callback: ApprovalCallback | None = None,
    timeout_ms: int = DEFAULT_TIMEOUT_MS,
    _command_runner: SetupCommandRunner | None = None,
) -> dict[str, Any]:
    """Install optional browser support through approved local commands."""
    commands = list(BROWSER_SETUP_COMMANDS)
    try:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Project root must be an existing directory.")
        normalized_runtime = runtime_mode.strip().lower()
        if normalized_runtime not in {"local", "docker", "no-command"}:
            raise ValueError(f"Unsupported runtime mode: {runtime_mode}")
    except (AttributeError, TypeError, ValueError) as exc:
        return _setup_result(
            ok=False,
            commands=commands,
            results=[],
            error=str(exc),
        )

    effective_permission_mode = permission_mode
    if normalized_runtime == "no-command":
        effective_permission_mode = "no-command"
    permissions = PermissionManager(
        mode=effective_permission_mode,
        approval_callback=approval_callback,
    )
    command_runner = _command_runner or run_command
    results: list[dict[str, Any]] = []

    for command in commands:
        decision = permissions.authorize(
            PermissionLevel.EXECUTE,
            "run_command",
            {"command": command},
        )
        if not decision.allowed:
            error = decision.reason or "Browser setup command was not approved."
            results.append(
                {
                    "ok": False,
                    "command": command,
                    "permission_denied": True,
                    "error": error,
                }
            )
            return _setup_result(
                ok=False,
                commands=commands,
                results=results,
                error=error,
                permission_denied=True,
            )

        # Browser validation runs on the host, so its optional Python package
        # and Chromium binary must be installed through the local runner. A
        # configured no-command runtime has already been denied above.
        command_result = command_runner(
            root,
            command,
            timeout_ms,
            runtime_mode="local",
            allow_network=False,
        )
        result = dict(command_result)
        results.append(result)
        if result.get("ok") is not True:
            error = str(result.get("error") or "Browser setup command failed.")
            return _setup_result(
                ok=False,
                commands=commands,
                results=results,
                error=error,
            )

    return _setup_result(
        ok=True,
        commands=commands,
        results=results,
    )


def run_browser_validation(
    url: str,
    screenshot: bool = True,
    checks: Sequence[str] | None = None,
    *,
    full_page: bool = False,
    width: int = DEFAULT_VIEWPORT_WIDTH,
    height: int = DEFAULT_VIEWPORT_HEIGHT,
    project_root: str | Path = ".",
    _playwright_factory: PlaywrightFactory | None = None,
) -> dict[str, Any]:
    """Inspect an existing loopback URL without starting a development server.

    "checks" is an optional list of CSS selectors expected to match at least
    one element. ``full_page`` controls whether the screenshot includes the
    entire scrollable page. Playwright is imported only when this function is
    called.
    """
    try:
        local_url = _validated_local_url(url)
        normalized_checks = _validated_checks(checks)
        if not isinstance(full_page, bool):
            raise TypeError("Browser full_page must be a boolean.")
        viewport = _validated_viewport(width, height)
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Project root must be an existing directory.")
    except (TypeError, ValueError) as exc:
        return _error_result(str(exc))

    factory = _playwright_factory or _load_playwright_factory()
    if factory is None:
        return _error_result(_PLAYWRIGHT_SETUP_ERROR)

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
                page = browser.new_page(viewport=viewport)
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
                    page.screenshot(
                        path=str(screenshot_file),
                        full_page=full_page,
                    )
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


def run_managed_browser_validation(
    command: str,
    url: str,
    screenshot: bool = True,
    checks: Sequence[str] | None = None,
    *,
    full_page: bool = False,
    width: int = DEFAULT_VIEWPORT_WIDTH,
    height: int = DEFAULT_VIEWPORT_HEIGHT,
    startup_timeout_ms: int = DEFAULT_SERVER_STARTUP_TIMEOUT_MS,
    project_root: str | Path = ".",
    approval_callback: ApprovalCallback | None = None,
    _playwright_factory: PlaywrightFactory | None = None,
    _popen_factory: PopenFactory | None = None,
    _url_probe: URLProbe | None = None,
    _clock: Callable[[], float] | None = None,
    _sleep: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """Approve, start, validate, and stop one project-local dev server."""
    try:
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Managed server command must be a non-empty string.")
        normalized_command = command.strip()
        if len(normalized_command) > MAX_SERVER_OUTPUT_CHARACTERS:
            raise ValueError("Managed server command is too long.")
        local_url = _validated_local_url(url)
        _validated_checks(checks)
        if not isinstance(full_page, bool):
            raise TypeError("Browser full_page must be a boolean.")
        _validated_viewport(width, height)
        timeout_ms = _validated_server_timeout(startup_timeout_ms)
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Project root must be an existing directory.")
    except (TypeError, ValueError) as exc:
        return _managed_error_result(str(exc))

    factory = _playwright_factory or _load_playwright_factory()
    if factory is None:
        return _managed_error_result(_PLAYWRIGHT_SETUP_ERROR)

    decision = PermissionManager(
        mode="default",
        approval_callback=approval_callback,
    ).authorize(
        PermissionLevel.EXECUTE,
        "run_managed_browser_validation",
        {"command": normalized_command},
    )
    if not decision.allowed:
        result = _managed_error_result(
            decision.reason or "Managed server command was not approved."
        )
        result["permission_denied"] = True
        return result

    return _run_approved_managed_browser_validation(
        normalized_command,
        local_url,
        screenshot=screenshot,
        checks=checks,
        full_page=full_page,
        width=width,
        height=height,
        startup_timeout_ms=timeout_ms,
        project_root=root,
        _playwright_factory=factory,
        _popen_factory=_popen_factory,
        _url_probe=_url_probe,
        _clock=_clock,
        _sleep=_sleep,
    )


def _run_approved_managed_browser_validation(
    command: str,
    url: str,
    *,
    screenshot: bool,
    checks: Sequence[str] | None,
    full_page: bool,
    width: int,
    height: int,
    startup_timeout_ms: int,
    project_root: Path,
    _playwright_factory: PlaywrightFactory,
    _popen_factory: PopenFactory | None,
    _url_probe: URLProbe | None,
    _clock: Callable[[], float] | None,
    _sleep: Callable[[float], None] | None,
) -> dict[str, Any]:
    """Run a managed server only after its caller has obtained approval."""
    try:
        arguments = _server_arguments(command, project_root)
    except ValueError as exc:
        return _managed_error_result(str(exc))

    popen_factory = _popen_factory or subprocess.Popen
    clock = _clock or time.monotonic
    sleep = _sleep or time.sleep
    probe = _url_probe or _probe_local_url
    try:
        process = popen_factory(
            arguments,
            cwd=project_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
    except OSError as exc:
        return _managed_error_result(
            "Could not start the managed dev server: "
            f"{_safe_process_error(exc)}"
        )

    stdout_collector, stdout_thread = _start_output_collector(process.stdout)
    stderr_collector, stderr_thread = _start_output_collector(process.stderr)
    deadline = clock() + (startup_timeout_ms / 1000)
    ready = False
    startup_error: str | None = None
    while True:
        exit_code = process.poll()
        if exit_code is not None:
            startup_error = (
                "Managed dev server exited before the URL responded "
                f"(exit code {exit_code})."
            )
            break
        remaining = deadline - clock()
        if remaining <= 0:
            startup_error = (
                "Managed dev server did not respond within "
                f"{startup_timeout_ms} ms."
            )
            break
        try:
            ready = probe(url, min(SERVER_PROBE_TIMEOUT_SECONDS, remaining))
        except Exception:
            ready = False
        if ready:
            break
        sleep(min(SERVER_PROBE_INTERVAL_SECONDS, remaining))

    if startup_error is not None:
        stopped = _stop_server_process(process)
        _join_output_thread(stdout_thread, stdout_collector)
        _join_output_thread(stderr_thread, stderr_collector)
        result = _managed_error_result(startup_error)
        result["managed_server"] = _server_result(
            process,
            stdout_collector,
            stderr_collector,
            ready=False,
            stopped=stopped,
        )
        return result

    try:
        result = run_browser_validation(
            url,
            screenshot=screenshot,
            checks=checks,
            full_page=full_page,
            width=width,
            height=height,
            project_root=project_root,
            _playwright_factory=_playwright_factory,
        )
    finally:
        stopped = _stop_server_process(process)
        _join_output_thread(stdout_thread, stdout_collector)
        _join_output_thread(stderr_thread, stderr_collector)
    result["managed_server"] = _server_result(
        process,
        stdout_collector,
        stderr_collector,
        ready=True,
        stopped=stopped,
    )
    return result


class _BoundedOutputCollector:
    def __init__(self) -> None:
        self._parts: list[str] = []
        self._length = 0
        self.truncated = False

    def drain(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(1_024)
                if not chunk:
                    break
                text = chunk if isinstance(chunk, str) else str(chunk)
                remaining = MAX_SERVER_OUTPUT_CHARACTERS - self._length
                if remaining > 0:
                    kept = text[:remaining]
                    self._parts.append(kept)
                    self._length += len(kept)
                if len(text) > remaining:
                    self.truncated = True
        except Exception:
            self.truncated = True

    def value(self) -> str:
        return "".join(self._parts)


def _start_output_collector(
    stream: Any,
) -> tuple[_BoundedOutputCollector, Thread | None]:
    collector = _BoundedOutputCollector()
    if stream is None:
        return collector, None
    thread = Thread(target=collector.drain, args=(stream,), daemon=True)
    thread.start()
    return collector, thread


def _join_output_thread(
    thread: Thread | None,
    collector: _BoundedOutputCollector,
) -> None:
    if thread is None:
        return
    thread.join(timeout=1)
    if thread.is_alive():
        collector.truncated = True


def _server_arguments(command: str, project_root: Path) -> list[str]:
    dangerous_pattern = dangerous_command_reason(command)
    if dangerous_pattern is None:
        dangerous_pattern = normalized_dangerous_command_reason(command)
    if dangerous_pattern is not None:
        raise ValueError(
            "Managed server command blocked by safety policy: matched "
            f"prohibited pattern {dangerous_pattern!r}."
        )
    try:
        arguments = split_command(command)
    except ValueError as exc:
        raise ValueError(f"Could not parse managed server command: {exc}") from exc
    if not arguments:
        raise ValueError("Managed server command must not be empty.")
    normalized_pattern = dangerous_command_reason(" ".join(arguments))
    if normalized_pattern is not None:
        raise ValueError(
            "Managed server command blocked after argument normalization: "
            f"matched prohibited pattern {normalized_pattern!r}."
        )
    executable = arguments[0]
    resolved = resolve_executable(executable, project_root)
    if resolved is None:
        safe_executable, _ = _bounded_text(
            _redacted_text(executable),
            MAX_LOG_TEXT_CHARACTERS,
        )
        raise ValueError(
            f"Managed server executable {safe_executable!r} was not found. "
            f"{executable_path_summary()}"
        )
    arguments[0] = resolved
    return arguments


def _validated_server_timeout(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("Managed server startup_timeout_ms must be an integer.")
    if not 1 <= value <= MAX_SERVER_STARTUP_TIMEOUT_MS:
        raise ValueError(
            "Managed server startup_timeout_ms must be between 1 and "
            f"{MAX_SERVER_STARTUP_TIMEOUT_MS}."
        )
    return value


def _stop_server_process(process: Any) -> bool:
    try:
        if process.poll() is not None:
            return True
        process.terminate()
        process.wait(timeout=SERVER_STOP_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=SERVER_STOP_TIMEOUT_SECONDS)
        except Exception:
            return False
    except Exception:
        try:
            process.kill()
        except Exception:
            return False
    try:
        return process.poll() is not None
    except Exception:
        return False


def _server_result(
    process: Any,
    stdout: _BoundedOutputCollector,
    stderr: _BoundedOutputCollector,
    *,
    ready: bool,
    stopped: bool,
) -> dict[str, Any]:
    try:
        exit_code = process.poll()
    except Exception:
        exit_code = None
    stdout_text, stdout_truncated = _bounded_text(
        _redacted_text(stdout.value()),
        MAX_SERVER_OUTPUT_CHARACTERS,
    )
    stderr_text, stderr_truncated = _bounded_text(
        _redacted_text(stderr.value()),
        MAX_SERVER_OUTPUT_CHARACTERS,
    )
    return {
        "started": True,
        "ready": ready,
        "stopped": stopped,
        "exit_code": exit_code,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "output_truncated": (
            stdout.truncated
            or stderr.truncated
            or stdout_truncated
            or stderr_truncated
        ),
    }


def _managed_error_result(error: str) -> dict[str, Any]:
    result = _error_result(error)
    result["managed_server"] = {
        "started": False,
        "ready": False,
        "stopped": False,
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "output_truncated": False,
    }
    return result


def _safe_process_error(exc: Exception) -> str:
    message, _ = _bounded_text(
        _redacted_text(exc),
        MAX_LOG_TEXT_CHARACTERS,
    )
    return f"{type(exc).__name__}: {message}"


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        return None


def _probe_local_url(url: str, timeout_seconds: float) -> bool:
    request = Request(url, method="GET")
    opener = build_opener(_NoRedirectHandler())
    try:
        with opener.open(request, timeout=max(0.001, timeout_seconds)) as response:
            response.read(1)
        return True
    except HTTPError:
        # Any HTTP response proves that the local server is listening. Redirects
        # are deliberately not followed, so a local page cannot make this probe
        # contact a non-loopback host.
        return True
    except (OSError, URLError, ValueError):
        return False


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


def _validated_viewport(width: int, height: int) -> dict[str, int]:
    return {
        "width": _validated_viewport_dimension(
            width,
            name="width",
            minimum=MIN_VIEWPORT_WIDTH,
            maximum=MAX_VIEWPORT_WIDTH,
        ),
        "height": _validated_viewport_dimension(
            height,
            name="height",
            minimum=MIN_VIEWPORT_HEIGHT,
            maximum=MAX_VIEWPORT_HEIGHT,
        ),
    }


def _validated_viewport_dimension(
    value: int,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"Viewport {name} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValueError(
            f"Viewport {name} must be between {minimum} and {maximum} pixels."
        )
    return value


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
    normalized = redacted.casefold()
    if (
        "executable doesn't exist" in normalized
        or "browser executable" in normalized
        or "playwright install" in normalized
    ):
        return (
            "Playwright's Chromium browser is unavailable. Run "
            "'python -m playwright install chromium'."
        )
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


def _setup_result(
    *,
    ok: bool,
    commands: list[str],
    results: list[dict[str, Any]],
    error: str | None = None,
    permission_denied: bool = False,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": ok,
        "status": "passed" if ok else "failed",
        "commands": commands,
        "completed_commands": sum(item.get("ok") is True for item in results),
        "results": results,
    }
    if error is not None:
        bounded_error, _ = _bounded_text(error, MAX_LOG_TEXT_CHARACTERS)
        result["error"] = bounded_error
    if permission_denied:
        result["permission_denied"] = True
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
        "status": "passed" if ok else "failed",
        "title": title,
        "final_url": final_url,
        "console_errors": console_errors,
        "failed_requests": failed_requests,
        "screenshot_path": screenshot_path,
        "checks": checks,
        "truncated": truncated,
    }
