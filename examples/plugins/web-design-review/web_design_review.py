"""Bounded, read-only website source review heuristics.

The plugin intentionally uses only Python's standard library plus LunarForge's
existing project-root path guard. It does not execute project code.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from html import unescape
from pathlib import Path
from typing import Any

from lunar_forge.tools.files import IGNORED_DIRECTORIES, safe_path


MAX_FILES = 20
MAX_FILE_BYTES = 200_000
MAX_TOTAL_BYTES = 600_000
MAX_FINDINGS = 50
MAX_CANDIDATE_FINDINGS = 100

_MARKUP_SUFFIXES = frozenset({".html", ".htm", ".jsx", ".tsx"})
_STYLE_SUFFIXES = frozenset({".css", ".scss", ".sass"})
_SUPPORTED_SUFFIXES = _MARKUP_SUFFIXES | _STYLE_SUFFIXES
_SECRET_NAMES = frozenset(
    {
        ".env",
        ".npmrc",
        ".pypirc",
        "credentials.json",
        "secrets.json",
        "secrets.yaml",
        "secrets.yml",
    }
)
_CATEGORIES = frozenset(
    {
        "accessibility",
        "layout",
        "copy",
        "responsive",
        "semantics",
        "visual_hierarchy",
    }
)
_WEAK_CTA_COPY = frozenset(
    {"click here", "continue", "go", "learn more", "more", "read more", "submit"}
)


def review_files(
    files: list[str],
    focus: str = "general",
    *,
    _project_root: Path,
) -> dict[str, Any]:
    """Review bounded website files beneath the injected target project root."""
    if not isinstance(files, list) or not all(
        isinstance(item, str) for item in files
    ):
        return _empty_result(False, "files must be a list of strings.")
    if not isinstance(focus, str):
        return _empty_result(False, "focus must be a string.")

    normalized_focus = focus.strip().casefold()[:80] or "general"
    root = Path(_project_root).expanduser().resolve()
    findings: list[dict[str, Any]] = []
    files_reviewed: list[str] = []
    files_skipped: list[dict[str, str]] = []
    total_bytes = 0
    unsafe_path_requested = False

    for requested_file in files[:MAX_FILES]:
        display_request = requested_file.strip()[:240] or "[empty path]"
        if not requested_file.strip():
            files_skipped.append(
                {"file": display_request, "reason": "path is empty"}
            )
            continue
        try:
            target = safe_path(root, requested_file)
        except (OSError, PermissionError, ValueError):
            unsafe_path_requested = True
            files_skipped.append(
                {
                    "file": display_request,
                    "reason": "path is outside the project root",
                }
            )
            continue

        relative = target.relative_to(root)
        display_path = relative.as_posix()
        if _is_blocked_path(relative):
            files_skipped.append(
                {
                    "file": display_path,
                    "reason": "path is in a blocked runtime or secret location",
                }
            )
            continue
        if target.suffix.casefold() not in _SUPPORTED_SUFFIXES:
            files_skipped.append(
                {"file": display_path, "reason": "unsupported file type"}
            )
            continue
        if not target.exists():
            files_skipped.append(
                {"file": display_path, "reason": "file was not found"}
            )
            continue
        if not target.is_file():
            files_skipped.append(
                {"file": display_path, "reason": "path is not a file"}
            )
            continue

        try:
            file_size = target.stat().st_size
        except OSError:
            files_skipped.append(
                {"file": display_path, "reason": "file metadata could not be read"}
            )
            continue
        if file_size > MAX_FILE_BYTES:
            files_skipped.append(
                {"file": display_path, "reason": "file exceeds the size limit"}
            )
            continue
        if total_bytes + file_size > MAX_TOTAL_BYTES:
            files_skipped.append(
                {"file": display_path, "reason": "total review size limit reached"}
            )
            continue

        try:
            payload = target.read_bytes()
            content = payload.decode("utf-8")
        except (OSError, UnicodeError):
            files_skipped.append(
                {"file": display_path, "reason": "file is not readable UTF-8 text"}
            )
            continue
        if "\x00" in content:
            files_skipped.append(
                {"file": display_path, "reason": "file appears to be binary"}
            )
            continue

        total_bytes += len(payload)
        files_reviewed.append(display_path)
        if target.suffix.casefold() in _STYLE_SUFFIXES:
            _review_stylesheet(content, display_path, findings)
        else:
            _review_markup(
                content,
                display_path,
                findings,
                full_document=target.suffix.casefold() in {".html", ".htm"},
            )

    if len(files) > MAX_FILES:
        files_skipped.append(
            {
                "file": f"[{len(files) - MAX_FILES} additional files]",
                "reason": f"review accepts at most {MAX_FILES} files",
            }
        )

    findings = _prioritize(findings, normalized_focus)[:MAX_FINDINGS]
    scores = _score(findings)
    ok = not unsafe_path_requested
    summary = _summary(
        ok=ok,
        reviewed=len(files_reviewed),
        skipped=len(files_skipped),
        findings=len(findings),
        focus=normalized_focus,
    )
    return {
        "ok": ok,
        "summary": summary,
        "score": scores,
        "findings": findings,
        "files_reviewed": files_reviewed,
        "files_skipped": files_skipped,
    }


def _empty_result(ok: bool, summary: str) -> dict[str, Any]:
    return {
        "ok": ok,
        "summary": summary,
        "score": {
            "accessibility": 10,
            "visual_hierarchy": 10,
            "responsive": 10,
            "content_clarity": 10,
        },
        "findings": [],
        "files_reviewed": [],
        "files_skipped": [],
    }


def _is_blocked_path(relative: Path) -> bool:
    folded_parts = {part.casefold() for part in relative.parts}
    ignored = {part.casefold() for part in IGNORED_DIRECTORIES}
    filename = relative.name.casefold()
    return bool(folded_parts & ignored) or (
        filename in _SECRET_NAMES
        or filename.startswith(".env.")
        or "credential" in filename
        or "secret" in filename
    )


def _review_markup(
    content: str,
    path: str,
    findings: list[dict[str, Any]],
    *,
    full_document: bool,
) -> None:
    if full_document:
        if not re.search(r"<title\b[^>]*>\s*[^<\s][^<]*</title\s*>", content, re.I):
            _add(
                findings,
                "warning",
                "copy",
                path,
                1,
                "Add a concise, descriptive <title>.",
            )
        html_tag = re.search(r"<html\b([^>]*)>", content, re.I)
        if html_tag and not _has_named_attribute(html_tag.group(1), "lang"):
            _add(
                findings,
                "warning",
                "accessibility",
                path,
                _line(content, html_tag.start()),
                "Declare the page language on <html>.",
            )
        if not re.search(
            r"<meta\b(?=[^>]*\bname\s*=\s*['\"]viewport['\"])[^>]*>",
            content,
            re.I,
        ):
            _add(
                findings,
                "warning",
                "responsive",
                path,
                1,
                "Add a viewport meta tag for mobile layouts.",
            )

    for image in re.finditer(r"<img\b([^>]*)/?>", content, re.I | re.S):
        if not _has_named_attribute(image.group(1), "alt"):
            _add(
                findings,
                "warning",
                "accessibility",
                path,
                _line(content, image.start()),
                "Image is missing an alt attribute.",
            )

    labels = {
        value
        for match in re.finditer(r"<label\b([^>]*)>", content, re.I | re.S)
        if (value := _attribute(match.group(1), "for", "htmlFor"))
    }
    wrapped_label_ranges = [
        (match.start(), match.end())
        for match in re.finditer(
            r"<label\b[^>]*>.*?</label\s*>",
            content,
            re.I | re.S,
        )
    ]
    for field in re.finditer(r"<input\b([^>]*)/?>", content, re.I | re.S):
        attributes = field.group(1)
        input_type = (_attribute(attributes, "type") or "text").casefold()
        if input_type in {"button", "hidden", "image", "reset", "submit"}:
            continue
        field_id = _attribute(attributes, "id")
        has_wrapping_label = any(
            start <= field.start() < end for start, end in wrapped_label_ranges
        )
        if not (
            _accessible_name(attributes)
            or (field_id is not None and field_id in labels)
            or has_wrapping_label
        ):
            _add(
                findings,
                "warning",
                "accessibility",
                path,
                _line(content, field.start()),
                "Input needs a label or accessible name.",
            )

    for form in re.finditer(r"<form\b([^>]*)>", content, re.I | re.S):
        if not _accessible_name(form.group(1)):
            _add(
                findings,
                "info",
                "semantics",
                path,
                _line(content, form.start()),
                "Consider an accessible name when the form purpose is not obvious.",
            )

    actions = list(_elements(content, ("a", "button")))
    for tag, attributes, inner, start in actions:
        text = _visible_text(inner)
        if not text and not _accessible_name(attributes):
            _add(
                findings,
                "warning",
                "accessibility",
                path,
                _line(content, start),
                f"<{tag}> needs visible text or an accessible name.",
            )
        elif text.casefold().strip(" .!") in _WEAK_CTA_COPY:
            _add(
                findings,
                "info",
                "copy",
                path,
                _line(content, start),
                f'CTA copy "{text[:40]}" could describe the action more clearly.',
            )

    page_content = _has_page_content(content)
    headings = [
        (int(match.group(1)), match.start())
        for match in re.finditer(r"<h([1-6])\b[^>]*>", content, re.I)
    ]
    if page_content and not any(level == 1 for level, _ in headings):
        _add(
            findings,
            "warning",
            "visual_hierarchy",
            path,
            1,
            "Add one clear h1 for the page's primary heading.",
        )
    if headings and headings[0][0] != 1:
        _add(
            findings,
            "info",
            "visual_hierarchy",
            path,
            _line(content, headings[0][1]),
            "The first heading should usually be an h1.",
        )
    for previous, current in zip(headings, headings[1:]):
        if current[0] > previous[0] + 1:
            _add(
                findings,
                "warning",
                "visual_hierarchy",
                path,
                _line(content, current[1]),
                f"Heading level jumps from h{previous[0]} to h{current[0]}.",
            )

    if page_content and not re.search(r"<main\b", content, re.I):
        _add(
            findings,
            "warning",
            "semantics",
            path,
            1,
            "Wrap the primary page content in a <main> landmark.",
        )
    link_count = sum(1 for tag, _, _, _ in actions if tag == "a")
    if link_count >= 3 and not re.search(r"<nav\b", content, re.I):
        _add(
            findings,
            "info",
            "semantics",
            path,
            1,
            "Group the primary navigation links in a <nav> landmark.",
        )
    if full_document and page_content:
        if not re.search(r"<header\b", content, re.I):
            _add(
                findings,
                "info",
                "semantics",
                path,
                1,
                "Consider a <header> landmark for introductory content.",
            )
        if len(_visible_text(content)) > 300 and not re.search(
            r"<footer\b", content, re.I
        ):
            _add(
                findings,
                "info",
                "semantics",
                path,
                None,
                "Consider a <footer> landmark for closing information.",
            )
    if page_content and not actions:
        _add(
            findings,
            "info",
            "copy",
            path,
            None,
            "No clear call to action was found.",
        )


def _review_stylesheet(
    content: str,
    path: str,
    findings: list[dict[str, Any]],
) -> None:
    responsive_patterns = (
        r"@media\b",
        r"@container\b",
        r"\bclamp\s*\(",
        r"\bminmax\s*\(",
        r"\b(?:auto-fit|auto-fill)\b",
        r"\bflex-wrap\s*:",
    )
    if not any(re.search(pattern, content, re.I) for pattern in responsive_patterns):
        _add(
            findings,
            "warning",
            "responsive",
            path,
            1,
            "No media query or clear responsive layout rule was found.",
        )

    tiny_count = 0
    for match in re.finditer(
        r"\bfont-size\s*:\s*(\d*\.?\d+)\s*(px|rem|em)\b",
        content,
        re.I,
    ):
        value = float(match.group(1))
        unit = match.group(2).casefold()
        if (unit == "px" and 0 < value < 12) or (
            unit in {"rem", "em"} and 0 < value < 0.75
        ):
            _add(
                findings,
                "warning",
                "accessibility",
                path,
                _line(content, match.start()),
                f"Font size {match.group(1)}{unit} may be too small to read.",
            )
            tiny_count += 1
            if tiny_count >= 5:
                break


def _elements(
    content: str,
    tags: Iterable[str],
) -> Iterable[tuple[str, str, str, int]]:
    tag_group = "|".join(re.escape(tag) for tag in tags)
    pattern = re.compile(
        rf"<(?P<tag>{tag_group})\b(?P<attrs>[^>]*)>"
        rf"(?P<inner>.*?)</(?P=tag)\s*>",
        re.I | re.S,
    )
    for match in pattern.finditer(content):
        yield (
            match.group("tag").casefold(),
            match.group("attrs"),
            match.group("inner"),
            match.start(),
        )


def _attribute(attributes: str, *names: str) -> str | None:
    for name in names:
        match = re.search(
            rf"(?:^|\s){re.escape(name)}\s*=\s*"
            r"(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))",
            attributes,
            re.I | re.S,
        )
        if match:
            return next(
                (value for value in match.groups() if value is not None),
                "",
            )
    return None


def _has_named_attribute(attributes: str, *names: str) -> bool:
    return any(
        re.search(rf"(?:^|\s){re.escape(name)}(?:\s*=|\s|$)", attributes, re.I)
        for name in names
    )


def _accessible_name(attributes: str) -> bool:
    return any(
        bool((_attribute(attributes, name) or "").strip())
        for name in ("aria-label", "aria-labelledby", "title")
    )


def _visible_text(content: str) -> str:
    text = re.sub(r"<!--.*?-->", " ", content, flags=re.S)
    text = re.sub(r"<img\b([^>]*)>", _image_text, text, flags=re.I | re.S)
    text = re.sub(r"\{[^{}]+\}", " dynamic text ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(unescape(text).split())


def _image_text(match: re.Match[str]) -> str:
    return f" {_attribute(match.group(1), 'alt') or ''} "


def _has_page_content(content: str) -> bool:
    if re.search(r"<(?:main|section|article|h[1-6]|p|form|button|a)\b", content, re.I):
        return True
    body = re.search(r"<body\b[^>]*>(.*?)</body\s*>", content, re.I | re.S)
    candidate = body.group(1) if body else content
    candidate = re.sub(
        r"<(?:script|style)\b[^>]*>.*?</(?:script|style)\s*>",
        " ",
        candidate,
        flags=re.I | re.S,
    )
    return bool(_visible_text(candidate))


def _line(content: str, offset: int) -> int:
    return content.count("\n", 0, offset) + 1


def _add(
    findings: list[dict[str, Any]],
    severity: str,
    category: str,
    path: str,
    line: int | None,
    message: str,
) -> None:
    if len(findings) >= MAX_CANDIDATE_FINDINGS:
        return
    if severity not in {"info", "warning"} or category not in _CATEGORIES:
        return
    findings.append(
        {
            "severity": severity,
            "category": category,
            "file": path,
            "line": line,
            "message": message[:240],
        }
    )


def _prioritize(
    findings: list[dict[str, Any]],
    focus: str,
) -> list[dict[str, Any]]:
    focus_categories: set[str] = set()
    if "access" in focus or "semantic" in focus:
        focus_categories.update({"accessibility", "semantics"})
    if "responsive" in focus or "mobile" in focus:
        focus_categories.update({"responsive", "layout"})
    if "visual" in focus or "hierarchy" in focus or "layout" in focus:
        focus_categories.update({"visual_hierarchy", "layout"})
    if "copy" in focus or "content" in focus:
        focus_categories.add("copy")
    return sorted(
        findings,
        key=lambda item: (
            item["category"] not in focus_categories,
            item["severity"] != "warning",
            item["file"],
            item["line"] is None,
            item["line"] or 0,
        ),
    )


def _score(findings: list[dict[str, Any]]) -> dict[str, int]:
    score = {
        "accessibility": 10,
        "visual_hierarchy": 10,
        "responsive": 10,
        "content_clarity": 10,
    }
    for finding in findings:
        deduction = 2 if finding["severity"] == "warning" else 1
        category = finding["category"]
        if category in {"accessibility", "semantics"}:
            score["accessibility"] -= deduction
        if category in {"layout", "visual_hierarchy"}:
            score["visual_hierarchy"] -= deduction
        if category in {"layout", "responsive"}:
            score["responsive"] -= deduction
        if category == "copy":
            score["content_clarity"] -= deduction
    return {name: max(0, min(10, value)) for name, value in score.items()}


def _summary(
    *,
    ok: bool,
    reviewed: int,
    skipped: int,
    findings: int,
    focus: str,
) -> str:
    if not ok:
        return (
            f"Advisory source review: reviewed {reviewed} file(s), rejected "
            f"an unsafe path request, and reported {findings} finding(s)."
        )
    return (
        f"Advisory source review: reviewed {reviewed} file(s), skipped "
        f"{skipped}, and reported {findings} finding(s) with focus '{focus}'."
    )
