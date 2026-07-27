import ast
import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

import yaml

from lunar_forge.agent import CodeAgent
from lunar_forge.config import AppConfig, SubagentConfig, load_config
from lunar_forge.model_clients import ModelResponse, ToolCall
from lunar_forge.permissions import PermissionManager
from lunar_forge.plugins.loader import load_enabled_plugins
from lunar_forge.plugins.manifest import (
    load_plugin_manifest,
    parse_plugin_manifest,
)
from lunar_forge.plugins.registry import (
    build_plugin_diagnostic,
    register_plugin_tools,
    resolve_local_plugin_entrypoint,
)
from lunar_forge.plugins.sandbox import MAX_OUTPUT_CHARACTERS
from lunar_forge.tools.registry import ToolRegistry


EXAMPLE_PLUGIN = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "plugins"
    / "web-design-review"
)
BROWSER_DEMO = (
    Path(__file__).resolve().parents[1]
    / "examples"
    / "projects"
    / "browser-demo"
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class RecordingModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def complete(self, messages, tools=None):
        self.calls.append(
            {
                "messages": [dict(message) for message in messages],
                "tools": list(tools or []),
            }
        )
        return self.responses.pop(0)


def _install_example_plugin(project: Path) -> None:
    bundle = project / ".agent" / "plugins" / "web-design-review"
    bundle.mkdir(parents=True)
    for filename in ("plugin.yaml", "web_design_review.py"):
        shutil.copy2(EXAMPLE_PLUGIN / filename, bundle / filename)
    (project / ".agent" / "plugins.yaml").write_text(
        """
plugins:
  web_design:
    manifest: .agent/plugins/web-design-review/plugin.yaml
    enabled: true
""".lstrip(),
        encoding="utf-8",
    )


def _example_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    (checkout / ".git").mkdir(parents=True)
    plugin = checkout / "examples" / "plugins" / "web-design-review"
    shutil.copytree(EXAMPLE_PLUGIN, plugin)
    project = checkout / "examples" / "projects" / "browser-demo"
    shutil.copytree(
        BROWSER_DEMO,
        project,
        ignore=shutil.ignore_patterns(".agent", "dist", "node_modules"),
    )
    (project / ".agent").mkdir()
    shutil.copy2(
        plugin / "plugins.yaml.example",
        project / ".agent" / "plugins.yaml",
    )
    (project / ".agent" / "config.yaml").write_text(
        "plugins:\n  enabled: true\n",
        encoding="utf-8",
    )
    return project


def _invoke(project: Path, files: list[str], focus: str = "general"):
    loaded = load_enabled_plugins(project)
    registry = ToolRegistry(
        permission_manager=PermissionManager(
            approval_callback=lambda request: True,
        )
    )
    register_plugin_tools(
        registry,
        loaded,
        resolve_local_plugin_entrypoint,
    )
    return registry.execute(
        "web_design.review_files",
        {"files": files, "focus": focus},
    )


def test_example_plugin_manifest_loads_with_conservative_permissions(tmp_path):
    manifest_document = yaml.safe_load(
        (EXAMPLE_PLUGIN / "plugin.yaml").read_text(encoding="utf-8")
    )
    manifest = load_plugin_manifest(EXAMPLE_PLUGIN / "plugin.yaml")
    tool = manifest.tools[0]

    assert set(manifest_document) == {
        "name",
        "version",
        "description",
        "tools",
    }
    assert set(manifest_document["tools"][0]) == {
        "name",
        "description",
        "entrypoint",
        "parameters",
        "permissions",
    }
    assert set(manifest_document["tools"][0]["permissions"]) == {
        "filesystem",
        "commands",
        "network",
    }
    assert manifest.name == "web_design"
    assert tool.name == "web_design.review_files"
    assert tool.parameters["required"] == ["files"]
    assert tool.permissions.filesystem == "read"
    assert tool.permissions.commands is False
    assert tool.permissions.network is False

    _install_example_plugin(tmp_path)
    loaded = load_enabled_plugins(tmp_path)

    assert len(loaded) == 1
    assert loaded[0].project_root == tmp_path.resolve()
    assert loaded[0].manifest.tools[0].name == "web_design.review_files"


def test_documented_plugin_manifest_examples_match_loader_schema():
    examples = []
    for relative_path in ("README.md", "docs/manual-testing.md", "AGENTS.md"):
        document = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")
        manifest_sources = [
            match.group(1)
            for match in re.finditer(r"```yaml\s+(.*?)```", document, re.S)
        ]
        manifest_sources.extend(
            match.group(1)
            for match in re.finditer(
                r"@'\s*((?:(?!'@).)*?)\s*'@\s*\|\s*Set-Content"
                r"[^\n]*plugin\.yaml",
                document,
                re.S,
            )
        )
        for source in manifest_sources:
            decoded = yaml.safe_load(source)
            if (
                isinstance(decoded, dict)
                and {"name", "version", "description", "tools"} <= set(decoded)
            ):
                examples.append((relative_path, parse_plugin_manifest(decoded)))

    assert [path for path, _ in examples] == [
        "README.md",
        "docs/manual-testing.md",
        "AGENTS.md",
    ]
    assert all(manifest.tools for _, manifest in examples)


def test_browser_demo_one_copy_config_uses_plugin_without_browser_routing(
    tmp_path,
    monkeypatch,
):
    project = _example_checkout(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
    monkeypatch.delenv("LUNAR_FORGE_SUBAGENTS", raising=False)
    monkeypatch.delenv("LUNAR_FORGE_PARALLEL_SUBAGENTS", raising=False)
    config = load_config(project)
    loaded = load_enabled_plugins(project)
    diagnostic = build_plugin_diagnostic(
        project,
        globally_enabled=config.plugins.enabled,
    )
    approvals = []
    model = RecordingModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="design-review",
                        name="web_design_review_files",
                        arguments={
                            "files": [
                                "index.html",
                                r"src\App.jsx",
                                r"src\App.css",
                            ],
                            "focus": (
                                "accessibility, responsive layout, and "
                                "visual hierarchy"
                            ),
                        },
                    ),
                ),
            ),
            ModelResponse(text="Source review complete."),
        )
    )
    request = (
        "Use web_design.review_files to review index.html, src\\App.jsx, "
        "and src\\App.css for accessibility, responsive layout, and visual "
        "hierarchy. Do not edit files."
    )

    output = CodeAgent(
        config,
        model_client=model,
        approval_callback=lambda approval: approvals.append(approval) or True,
    ).run(request, project)

    first_schema_names = {
        schema["function"]["name"] for schema in model.calls[0]["tools"]
    }
    assert config.plugins.enabled is True
    assert len(loaded) == 1
    assert diagnostic["ok"] is True
    assert diagnostic["status"] == "passed"
    assert diagnostic["discovered_tools"][0]["internal_tool_name"] == (
        "web_design.review_files"
    )
    assert loaded[0].manifest_path == (
        project.parents[1] / "plugins" / "web-design-review" / "plugin.yaml"
    ).resolve()
    assert "web_design_review_files" in first_schema_names
    assert "run_browser_validation" not in first_schema_names
    assert "run_managed_browser_validation" not in first_schema_names
    assert "write_file" not in first_schema_names
    assert "run_command" not in first_schema_names
    assert len(approvals) == 1
    assert approvals[0].tool_name == "web_design.review_files"
    tool_message = next(
        message
        for message in model.calls[1]["messages"]
        if message["role"] == "tool"
    )
    tool_result = json.loads(tool_message["content"])
    assert tool_result["files_skipped"] == [
        {"file": "src/App.css", "reason": "file was not found"}
    ]
    assert output.startswith("Source review complete.")

    session_file = next((project / ".agent" / "sessions").glob("*.jsonl"))
    events = [
        json.loads(line)
        for line in session_file.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(
        event["event"] in {"browser_intent_detected", "readonly_fast_path"}
        for event in events
    )
    selection = next(
        event for event in events if event["event"] == "tool_schema_selection"
    )
    assert selection["data"]["task_profile"] == "review_only"


def test_plugin_traversal_final_summary_reports_only_path_safety_failure(
    tmp_path,
    monkeypatch,
):
    project = _example_checkout(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "empty-home")
    monkeypatch.delenv("LUNAR_FORGE_SUBAGENTS", raising=False)
    monkeypatch.delenv("LUNAR_FORGE_PARALLEL_SUBAGENTS", raising=False)
    approvals = []
    config = replace(
        load_config(project),
        subagents=SubagentConfig(enabled=True),
    )
    model = RecordingModel(
        (
            ModelResponse(
                text="",
                tool_calls=(
                    ToolCall(
                        id="unsafe-design-review",
                        name="web_design_review_files",
                        arguments={
                            "files": [r"..\..\outside.txt"],
                            "focus": "general",
                        },
                    ),
                ),
            ),
            ModelResponse(
                text=(
                    r"`..\..\outside.txt` is outside the accessible project "
                    "scope, and `web_design.review_files` is not available in "
                    "this environment, so no review could be performed."
                )
            ),
        )
    )

    output = CodeAgent(
        config,
        model_client=model,
        approval_callback=lambda approval: approvals.append(approval) or True,
    ).run(
        (
            r"Use web_design.review_files to review ..\..\outside.txt. "
            "Do not edit files."
        ),
        project,
    )

    assert output.startswith(
        r"Could not review ..\..\outside.txt: path is outside the project root."
    )
    assert "outside the project root" in output.casefold()
    assert "not available" not in output.casefold()
    assert "unavailable" not in output.casefold()
    assert "no web_design.review_files tool" not in output.casefold()
    assert config.plugins.enabled is True
    assert config.subagents.enabled is True
    assert len(approvals) == 1
    assert approvals[0].tool_name == "web_design.review_files"
    first_schema_names = {
        schema["function"]["name"] for schema in model.calls[0]["tools"]
    }
    assert "web_design_review_files" in first_schema_names
    assert "run_browser_validation" not in first_schema_names
    assert "run_managed_browser_validation" not in first_schema_names
    assert "write_file" not in first_schema_names
    assert "run_command" not in first_schema_names
    tool_message = next(
        message
        for message in model.calls[1]["messages"]
        if message["role"] == "tool"
    )
    tool_result = json.loads(tool_message["content"])
    assert tool_result["ok"] is False
    assert tool_result["files_skipped"] == [
        {
            "file": r"..\..\outside.txt",
            "reason": "path is outside the project root",
        }
    ]


def test_truly_unavailable_plugin_tool_summary_is_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv("LUNAR_FORGE_SUBAGENTS", raising=False)
    monkeypatch.delenv("LUNAR_FORGE_PARALLEL_SUBAGENTS", raising=False)
    approvals = []
    model = RecordingModel(
        (
            ModelResponse(
                text="The web_design.review_files tool is unavailable."
            ),
        )
    )

    output = CodeAgent(
        AppConfig(subagents=SubagentConfig(enabled=True)),
        model_client=model,
        approval_callback=lambda approval: approvals.append(approval) or True,
    ).run(
        "Use web_design.review_files to review index.html. Do not edit files.",
        tmp_path,
    )

    assert output.startswith(
        "The web_design.review_files tool is unavailable."
    )
    first_schema_names = {
        schema["function"]["name"] for schema in model.calls[0]["tools"]
    }
    assert "web_design_review_files" not in first_schema_names
    assert approvals == []


def test_example_plugin_reviews_html_css_and_jsx_fixture(tmp_path):
    _install_example_plugin(tmp_path)
    (tmp_path / "index.html").write_text(
        """
<html>
  <head></head>
  <body>
    <section>
      <h3>Welcome</h3>
      <img src="hero.png">
      <button></button>
      <form><input id="email"></form>
    </section>
  </body>
</html>
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "styles.css").write_text(
        "body { font-size: 10px; width: 900px; }\n",
        encoding="utf-8",
    )
    (tmp_path / "Card.jsx").write_text(
        """
export function Card() {
  return <main><h1>Card</h1><a href="/next"></a></main>
}
""".strip(),
        encoding="utf-8",
    )

    result = _invoke(
        tmp_path,
        ["index.html", "styles.css", "Card.jsx"],
        focus="accessibility",
    )

    assert result["ok"] is True
    assert result["files_reviewed"] == [
        "index.html",
        "styles.css",
        "Card.jsx",
    ]
    assert result["files_skipped"] == []
    assert {finding["category"] for finding in result["findings"]} >= {
        "accessibility",
        "responsive",
        "visual_hierarchy",
    }
    assert all(
        isinstance(value, int) and 0 <= value <= 10
        for value in result["score"].values()
    )


def test_example_plugin_reports_missing_and_unsupported_files_safely(tmp_path):
    _install_example_plugin(tmp_path)
    (tmp_path / "notes.txt").write_text("not a website source file", encoding="utf-8")

    result = _invoke(tmp_path, ["missing.html", "notes.txt"])

    assert result["ok"] is True
    assert result["files_reviewed"] == []
    skipped = {item["file"]: item["reason"] for item in result["files_skipped"]}
    assert skipped["missing.html"] == "file was not found"
    assert skipped["notes.txt"] == "unsupported file type"


def test_example_plugin_rejects_paths_outside_project_root(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    _install_example_plugin(project)
    outside = tmp_path / "outside.html"
    outside.write_text("<html><title>Outside</title></html>", encoding="utf-8")

    traversal = _invoke(project, ["../outside.html"])
    deep_traversal = _invoke(project, ["../../outside.txt"])
    absolute = _invoke(project, [str(outside.resolve())])

    for result in (traversal, deep_traversal, absolute):
        assert result["ok"] is False
        assert result["files_reviewed"] == []
        assert result["files_skipped"][0]["reason"] == (
            "path is outside the project root"
        )
        assert result["summary"].startswith("Advisory source review:")


def test_example_plugin_result_is_json_serializable_and_bounded(tmp_path):
    _install_example_plugin(tmp_path)
    images = "\n".join(
        f'<img src="image-{index}.png">' for index in range(80)
    )
    (tmp_path / "gallery.html").write_text(
        f"<html><body><main><h1>Gallery</h1>{images}</main></body></html>",
        encoding="utf-8",
    )

    result = _invoke(tmp_path, ["gallery.html"])
    encoded = json.dumps(result, allow_nan=False)

    assert result["ok"] is True
    assert len(result["findings"]) == 50
    assert len(encoded) <= MAX_OUTPUT_CHARACTERS
    assert all(
        set(finding) == {
            "severity",
            "category",
            "file",
            "line",
            "message",
        }
        for finding in result["findings"]
    )


def test_example_plugin_does_not_echo_large_source_contents(tmp_path):
    _install_example_plugin(tmp_path)
    sentinel = "PRIVATE_SOURCE_SENTINEL_7f3c"
    large_copy = " ".join(sentinel for _ in range(4_000))
    (tmp_path / "large.html").write_text(
        (
            '<html lang="en"><head><title>Large source</title>'
            '<meta name="viewport" content="width=device-width"></head>'
            f"<body><main><h1>Large source</h1><p>{large_copy}</p></main></body>"
            "</html>"
        ),
        encoding="utf-8",
    )

    result = _invoke(tmp_path, ["large.html"])
    encoded = json.dumps(result, allow_nan=False)

    assert result["ok"] is True
    assert result["files_reviewed"] == ["large.html"]
    assert result["summary"].startswith("Advisory source review:")
    assert sentinel not in encoded
    assert len(encoded) <= MAX_OUTPUT_CHARACTERS


def test_example_plugin_output_is_source_advice_not_browser_evidence(tmp_path):
    _install_example_plugin(tmp_path)
    (tmp_path / "page.html").write_text(
        "<html><body><h3>Page</h3><button></button></body></html>",
        encoding="utf-8",
    )

    result = _invoke(tmp_path, ["page.html"])
    encoded = json.dumps(result).casefold()

    assert result["ok"] is True
    assert result["summary"].startswith("Advisory source review:")
    assert all(
        finding["severity"] in {"info", "warning"}
        for finding in result["findings"]
    )
    assert all(
        evidence_claim not in encoded
        for evidence_claim in (
            "browser-rendered",
            "computed style",
            "pixel-perfect",
            "playwright observed",
            "screenshot proves",
        )
    )


def test_example_plugin_source_has_no_command_network_or_write_operations():
    source = (EXAMPLE_PLUGIN / "web_design_review.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        node.names[0].name.split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
    }
    imported_roots.update(
        (node.module or "").split(".", maxsplit=1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }

    assert imported_roots <= {
        "__future__",
        "collections",
        "html",
        "lunar_forge",
        "pathlib",
        "re",
        "typing",
    }
    assert called_attributes.isdisjoint(
        {
            "connect",
            "mkdir",
            "open",
            "popen",
            "remove",
            "rename",
            "request",
            "rmdir",
            "run",
            "system",
            "unlink",
            "urlopen",
            "write",
            "write_bytes",
            "write_text",
        }
    )
    assert called_names.isdisjoint(
        {"compile", "eval", "exec", "open", "__import__"}
    )
    assert not any(
        (EXAMPLE_PLUGIN / filename).exists()
        for filename in (
            "package.json",
            "pyproject.toml",
            "requirements.txt",
        )
    )
