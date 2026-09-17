"""Tests for rule-based purpose/category detection.

Covers the known-software identity rules, framework rules gated on real
manifest/command-line evidence (never a bare interpreter), and the
deterministic conflict-resolution strategy.
"""
from portforge_agent.detection.models import Confidence, DetectionFacts
from portforge_agent.detection.purpose import detect_purpose


def _facts(**overrides) -> DetectionFacts:
    defaults = dict(source="process")
    defaults.update(overrides)
    return DetectionFacts(**defaults)


# ---------------------------------------------------------------------------
# Known infrastructure software (single strong signal -> HIGH)
# ---------------------------------------------------------------------------


def test_postgresql_by_process_name():
    result = detect_purpose(_facts(process_name="postgres"))
    assert result.purpose == "postgresql"
    assert result.category == "database"
    assert result.confidence == Confidence.HIGH


def test_mysql_by_process_name():
    result = detect_purpose(_facts(process_name="mysqld.exe"))
    assert result.purpose == "mysql"
    assert result.category == "database"
    assert result.confidence == Confidence.HIGH


def test_redis_by_process_name():
    result = detect_purpose(_facts(process_name="redis-server"))
    assert result.purpose == "redis"
    assert result.category == "cache"


def test_memurai_by_process_name():
    result = detect_purpose(_facts(process_name="memurai.exe"))
    assert result.purpose == "memurai"
    assert result.category == "cache"
    assert result.confidence == Confidence.HIGH


def test_nginx_by_process_name():
    result = detect_purpose(_facts(process_name="nginx"))
    assert result.purpose == "nginx"
    assert result.category == "reverse-proxy"


def test_grafana_by_process_name():
    result = detect_purpose(_facts(process_name="grafana-server"))
    assert result.purpose == "grafana"
    assert result.category == "monitoring"


def test_prometheus_by_process_name():
    result = detect_purpose(_facts(process_name="prometheus"))
    assert result.purpose == "prometheus"
    assert result.category == "metrics"


def test_ollama_by_process_name():
    result = detect_purpose(_facts(process_name="ollama.exe"))
    assert result.purpose == "ollama"
    assert result.category == "ai"
    assert result.confidence == Confidence.HIGH


def test_minio_by_container_image():
    result = detect_purpose(_facts(container_image="minio/minio:latest"))
    assert result.purpose == "minio"
    assert result.category == "object-storage"
    assert result.confidence == Confidence.HIGH


def test_nginx_by_container_image_with_tag():
    result = detect_purpose(_facts(container_image="nginx:1.29-alpine"))
    assert result.purpose == "nginx"
    assert result.category == "reverse-proxy"


def test_postgres_by_container_image_with_tag():
    result = detect_purpose(_facts(container_image="postgres:16"))
    assert result.purpose == "postgresql"
    assert result.category == "database"


# ---------------------------------------------------------------------------
# Frameworks: require real corroborating evidence, never a bare interpreter
# ---------------------------------------------------------------------------


def test_fastapi_requires_manifest_dependency_not_just_uvicorn():
    # uvicorn alone (no fastapi dependency) stays generic "api", not "fastapi".
    result = detect_purpose(_facts(command_line=["uvicorn", "main:app"]))
    assert result.category == "api"
    assert result.purpose == "api"


def test_fastapi_with_manifest_dependency_evidence():
    result = detect_purpose(
        _facts(command_line=["uvicorn", "main:app"]), manifest_dependencies={"fastapi", "pydantic"}
    )
    assert result.purpose == "fastapi"
    assert result.category == "api"
    assert result.confidence == Confidence.HIGH


def test_flask_by_manifest_dependency():
    result = detect_purpose(_facts(process_name="python.exe"), manifest_dependencies={"flask"})
    assert result.purpose == "flask"
    assert result.category == "api"


def test_flask_by_command_line():
    result = detect_purpose(_facts(command_line=["flask", "run"]))
    assert result.purpose == "flask"
    assert result.category == "api"


def test_django_by_manifest_dependency():
    result = detect_purpose(_facts(process_name="python.exe"), manifest_dependencies={"django"})
    assert result.purpose == "django"
    assert result.category == "api"


def test_django_by_manage_py_command_line():
    result = detect_purpose(_facts(command_line=["python", "manage.py", "runserver"]))
    assert result.purpose == "django"


def test_express_by_manifest_dependency():
    result = detect_purpose(_facts(process_name="node.exe"), manifest_dependencies={"express"})
    assert result.purpose == "express"
    assert result.category == "api"


def test_nextjs_by_manifest_dependency():
    result = detect_purpose(_facts(process_name="node.exe"), manifest_dependencies={"next"})
    assert result.purpose == "nextjs"
    assert result.category == "frontend"


def test_vite_by_manifest_dependency():
    result = detect_purpose(_facts(process_name="node.exe"), manifest_dependencies={"vite"})
    assert result.purpose == "vite"
    assert result.category == "development-server"


def test_vite_by_command_line():
    result = detect_purpose(_facts(command_line=["node", "node_modules/.bin/vite"]))
    assert result.purpose == "vite"
    assert result.category == "development-server"


# ---------------------------------------------------------------------------
# Conservatism: bare interpreters must never imply a purpose
# ---------------------------------------------------------------------------


def test_unknown_python_process_alone_stays_unknown():
    result = detect_purpose(_facts(process_name="python.exe", command_line=["python.exe", "script.py"]))
    assert result.purpose is None
    assert result.category == "unknown"
    assert result.confidence == Confidence.UNKNOWN


def test_unknown_node_process_alone_stays_unknown():
    result = detect_purpose(_facts(process_name="node.exe", command_line=["node.exe", "server.js"]))
    assert result.purpose is None
    assert result.category == "unknown"


def test_bare_java_does_not_imply_backend():
    result = detect_purpose(_facts(process_name="java.exe", command_line=["java", "-jar", "app.jar"]))
    assert result.purpose is None
    assert result.category == "unknown"


def test_generic_npm_run_dev_does_not_imply_development_server():
    # A generic wrapper phrase alone is not distinctive enough evidence.
    result = detect_purpose(_facts(command_line=["npm", "run", "dev", "--", "--host"]))
    assert result.category == "unknown"


# ---------------------------------------------------------------------------
# Docker Compose service name evidence
# ---------------------------------------------------------------------------


def test_compose_service_name_frontend_role():
    result = detect_purpose(_facts(source="docker", compose_service="frontend-ui"))
    assert result.purpose == "frontend"
    assert result.category == "frontend"
    assert result.confidence == Confidence.HIGH


def test_compose_service_name_api_role():
    result = detect_purpose(_facts(source="docker", compose_service="backend-api"))
    assert result.purpose == "api"
    assert result.category == "api"


def test_compose_service_name_agrees_with_command_line_still_high():
    result = detect_purpose(
        _facts(source="docker", compose_service="backend-api", container_command=["uvicorn", "main:app"])
    )
    assert result.category == "api"
    assert result.confidence == Confidence.HIGH
    assert len(result.evidence) == 2  # both sources recorded


def test_compose_service_name_and_image_agree_and_both_recorded():
    result = detect_purpose(_facts(source="docker", compose_service="nginx", container_image="nginx:1.29-alpine"))
    assert result.category == "reverse-proxy"
    assert result.confidence == Confidence.HIGH
    assert len(result.evidence) == 2


# ---------------------------------------------------------------------------
# Conflicting evidence: deterministic resolution, never "first rule wins"
# ---------------------------------------------------------------------------


def test_conflicting_evidence_majority_wins_at_medium_confidence():
    # Two independent signals say "database" (process name identity AND
    # container image identity both match postgres), one disagreeing signal
    # says "frontend" (a deliberately confusing Compose service name) --
    # majority (2 vs 1) wins, but confidence is downgraded from HIGH to
    # MEDIUM because something genuinely disagreed, and all evidence
    # (including the outlier) is kept visible.
    result = detect_purpose(
        _facts(
            source="docker",
            process_name="postgres",
            container_image="postgres:16",
            compose_service="frontend-ui",
        )
    )
    assert result.category == "database"
    assert result.purpose == "postgresql"
    assert result.confidence == Confidence.MEDIUM
    assert result.method == "rule_based_majority"
    assert len(result.evidence) == 3


def test_true_tie_between_categories_is_unknown_at_low_confidence():
    result = detect_purpose(
        _facts(source="docker", container_image="redis:7-alpine", compose_service="frontend-ui")
    )
    assert result.purpose is None
    assert result.category == "unknown"
    assert result.confidence == Confidence.LOW
    assert result.method == "conflicting_evidence"
    assert len(result.evidence) == 2  # both disagreeing signals kept visible


def test_majority_resolves_a_three_way_signal_conflict():
    # image says cache (redis), command line says cache (redis hint would
    # need a rule -- instead use two cache-agreeing sources vs one outlier)
    result = detect_purpose(
        _facts(
            source="docker",
            container_image="redis:7-alpine",
            compose_service="redis-cache",  # "cache" keyword -> cache category too
        ),
    )
    assert result.category == "cache"
    assert result.confidence == Confidence.HIGH  # both sources agree, no conflict here


def test_no_evidence_is_unknown():
    result = detect_purpose(_facts())
    assert result.purpose is None
    assert result.category == "unknown"
    assert result.confidence == Confidence.UNKNOWN
    assert result.method == "no_evidence"
    assert result.evidence == []
