import json
import os
import subprocess
import sys


def _run(args, cwd):
    env = os.environ.copy()
    env.pop("PORTFORGE_ADMIN_BOOTSTRAP_TOKEN", None)
    return subprocess.run(
        [sys.executable, "-m", "portforge_agent", *args],
        cwd=cwd,
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_project_init_json_stdout_is_machine_readable(tmp_path):
    result = _run(["project", "init", "--stdout", "--json"], tmp_path)
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["created"] is False
    assert payload["manifest"]
    assert result.stderr == ""


def test_project_validate_json_error_is_machine_readable(tmp_path):
    (tmp_path / "portforge.yml").write_text("version: [", encoding="utf-8")
    result = _run(["project", "validate", "--json"], tmp_path)
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "MANIFEST_PARSE_ERROR"
    assert result.stderr == ""


def test_config_status_json_error_is_machine_readable(tmp_path):
    result = _run(
        ["config", "status", "00000000-0000-0000-0000-000000000000", "--project-root", str(tmp_path), "--json"],
        tmp_path,
    )
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "CONFIG_MUTATION_NOT_FOUND"
    assert result.stderr == ""
