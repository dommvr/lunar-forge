from pathlib import Path
from types import SimpleNamespace

import lunar_forge.runtime.docker_runner as docker_runner
from lunar_forge.runtime.docker_runner import build_docker_args, run_docker_command


def _successful_subprocess(calls):
    def run(arguments, **kwargs):
        calls.append((list(arguments), kwargs))
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    return run


def test_docker_args_use_isolated_network_and_only_project_mount(
    monkeypatch,
    tmp_path,
):
    calls = []
    monkeypatch.setattr(
        docker_runner.subprocess,
        "run",
        _successful_subprocess(calls),
    )

    result = run_docker_command(tmp_path, "python -m pytest -q")

    assert result["ok"] is True
    assert result["runtime"] == "docker"
    assert calls[0][0] == ["docker", "info"]
    docker_args = calls[1][0]
    assert docker_args[docker_args.index("--network") + 1] == "none"
    assert docker_args[docker_args.index("--memory") + 1] == "2g"
    assert docker_args[docker_args.index("--cpus") + 1] == "2"
    assert docker_args.count("-v") == 1
    assert docker_args[docker_args.index("-v") + 1] == (
        f"{tmp_path.resolve()}:/workspace"
    )
    assert docker_args[docker_args.index("-w") + 1] == "/workspace"
    assert "lunar-forge-sandbox" in docker_args
    assert "--privileged" not in docker_args
    assert "/var/run/docker.sock" not in " ".join(docker_args)
    assert all(call_kwargs["shell"] is False for _, call_kwargs in calls)


def test_allow_network_switches_docker_to_bridge(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        docker_runner.subprocess,
        "run",
        _successful_subprocess(calls),
    )

    result = run_docker_command(
        tmp_path,
        "npm test",
        allow_network=True,
    )

    docker_args = calls[1][0]
    assert result["network"] == "bridge"
    assert docker_args[docker_args.index("--network") + 1] == "bridge"


def test_docker_unavailable_returns_clear_error(monkeypatch, tmp_path):
    calls = []

    def unavailable(arguments, **kwargs):
        calls.append(list(arguments))
        raise FileNotFoundError("docker executable was not found")

    monkeypatch.setattr(docker_runner.subprocess, "run", unavailable)

    result = run_docker_command(tmp_path, "python --version")

    assert result["ok"] is False
    assert result["exit_code"] is None
    assert "Docker is unavailable" in result["error"]
    assert "docker info" in result["error"]
    assert calls == [["docker", "info"]]


def test_dangerous_command_is_blocked_before_docker_info(monkeypatch, tmp_path):
    def unexpected_run(*args, **kwargs):
        raise AssertionError("Docker must not be contacted")

    monkeypatch.setattr(docker_runner.subprocess, "run", unexpected_run)

    result = run_docker_command(tmp_path, "sudo python --version")

    assert result["ok"] is False
    assert "blocked by safety policy" in result["error"]

    raw_wrapper = run_docker_command(tmp_path, "docker run alpine")
    assert raw_wrapper["ok"] is False
    assert "raw docker run" in raw_wrapper["error"]


def test_docker_wrapper_refuses_host_home_mount():
    try:
        build_docker_args(Path.home(), "python --version")
    except PermissionError as exc:
        assert "host home" in str(exc)
    else:
        raise AssertionError("Host home directory mount was not rejected")
