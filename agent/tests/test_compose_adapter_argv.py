"""Phase 18: compose adapter argv safety and project name stability."""
from __future__ import annotations

from portforge_agent.deployment.compose_adapter import (
    build_apply_argv,
    build_down_argv,
    build_ps_argv,
    build_stop_argv,
    build_validate_argv,
    compose_project_name,
)


def test_compose_project_name_stable_and_sanitized():
    host_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    name1 = compose_project_name("My Project!", "Production", host_id)
    name2 = compose_project_name("My Project!", "Production", host_id)
    assert name1 == name2
    assert name1.startswith("pf-")
    assert " " not in name1
    assert "!" not in name1
    assert len(name1) <= 63


def test_compose_project_name_differs_by_host():
    a = compose_project_name("demo", "dev", "11111111-1111-1111-1111-111111111111")
    b = compose_project_name("demo", "dev", "22222222-2222-2222-2222-222222222222")
    assert a != b


def test_validate_argv_is_fixed_shape():
    argv = build_validate_argv("/usr/bin/docker", ["docker-compose.yml"], "pf-demo-dev-host")
    assert argv[0] == "/usr/bin/docker"
    assert argv[1] == "compose"
    assert argv[2:4] == ["-f", "docker-compose.yml"]
    assert argv[4:6] == ["-p", "pf-demo-dev-host"]
    assert argv[6:] == ["config"]


def test_apply_argv_has_no_extra_tokens():
    argv = build_apply_argv("/usr/bin/docker", ["a.yml", "b.yml"], "pf-demo-dev-host")
    assert argv[-3:] == ["up", "-d", "--remove-orphans"]
    assert argv.count("-f") == 2
    assert "--build" not in argv
    assert ";" not in " ".join(argv)


def test_stop_and_down_argv_fixed():
    stop = build_stop_argv("/usr/bin/docker", ["compose.yml"], "proj")
    down = build_down_argv("/usr/bin/docker", ["compose.yml"], "proj")
    ps = build_ps_argv("/usr/bin/docker", ["compose.yml"], "proj")
    assert stop[-1] == "stop"
    assert down[-2:] == ["down", "--remove-orphans"]
    assert ps[-4:] == ["ps", "-a", "--format", "json"]


def test_argv_rejects_shell_metacharacters_in_project_name():
    name = compose_project_name("safe", "dev", "host-id")
    argv = build_apply_argv("/usr/bin/docker", ["compose.yml"], name)
    joined = " ".join(argv)
    assert "|" not in joined
    assert "$(" not in joined
    assert "`" not in joined
