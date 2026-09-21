"""Phase 8C §37-39: Compose editor tests."""
from __future__ import annotations

import pytest

from portforge_agent.compose_editor import ComposeError, apply_port_mapping, dump_compose, load_compose

SHORT_SYNTAX = """services:
  api:
    image: myapi:latest
    ports:
      - "9000:8000"
    environment:
      - FOO=bar
  frontend:
    image: myfrontend:latest
    ports:
      - "3000:3000"
"""


def test_short_syntax_update_only_touches_matched_service():
    data = load_compose(SHORT_SYNTAX)
    data, change = apply_port_mapping(data, "api", 8000, "tcp", 8000)
    out = dump_compose(data)
    assert out == SHORT_SYNTAX.replace('"9000:8000"', '"8000:8000"')
    assert change.before_host == "9000"
    assert change.after_host == "8000"
    assert change.action == "update"


def test_short_syntax_with_ip_prefix_preserves_ip():
    text = "services:\n  api:\n    ports:\n      - \"127.0.0.1:9000:8000\"\n"
    data = load_compose(text)
    data, change = apply_port_mapping(data, "api", 8000, "tcp", 8000)
    out = dump_compose(data)
    assert '"127.0.0.1:8000:8000"' in out


def test_long_syntax_update_preserves_other_fields():
    text = (
        "services:\n"
        "  api:\n"
        "    ports:\n"
        "      - target: 8000\n"
        '        published: "9000"\n'
        "        protocol: tcp\n"
        "        mode: host\n"
    )
    data = load_compose(text)
    data, change = apply_port_mapping(data, "api", 8000, "tcp", 8000)
    out = dump_compose(data)
    assert "target: 8000" in out
    assert 'published: "8000"' in out
    assert "mode: host" in out
    assert change.before_host == "9000"


def test_ambiguous_mapping_fails_with_zero_mutation():
    text = 'services:\n  api:\n    ports:\n      - "9000:8000"\n      - "9001:8000"\n'
    data = load_compose(text)
    with pytest.raises(ComposeError) as exc_info:
        apply_port_mapping(data, "api", 8000, "tcp", 8080)
    assert exc_info.value.code == "COMPOSE_PORT_AMBIGUOUS"
    # zero mutation -- re-dump must equal the original
    assert dump_compose(data) == text


def test_no_matching_mapping_appends_new_entry_without_deleting_existing():
    text = 'services:\n  api:\n    ports:\n      - "9000:7000"\n'
    data = load_compose(text)
    data, change = apply_port_mapping(data, "api", 8000, "tcp", 8000)
    out = dump_compose(data)
    assert '"9000:7000"' in out  # unrelated mapping untouched
    assert '"8000:8000"' in out  # new mapping added
    assert change.action == "add"
    assert change.before_host is None


def test_unknown_service_fails():
    data = load_compose("services:\n  api:\n    ports: []\n")
    with pytest.raises(ComposeError) as exc_info:
        apply_port_mapping(data, "ghost", 8000, "tcp", 8000)
    assert exc_info.value.code == "COMPOSE_SERVICE_NOT_FOUND"


def test_invalid_yaml_fails():
    with pytest.raises(ComposeError) as exc_info:
        load_compose("services: [unterminated")
    assert exc_info.value.code == "CONFIG_PARSE_ERROR"


def test_udp_protocol_matched_independently_of_tcp():
    text = 'services:\n  api:\n    ports:\n      - "9000:8000/udp"\n      - "9000:8000"\n'
    data = load_compose(text)
    data, change = apply_port_mapping(data, "api", 8000, "udp", 8080)
    out = dump_compose(data)
    assert '"8080:8000/udp"' in out
    assert '"9000:8000"' in out  # the TCP mapping for the same container port untouched
    assert change.action == "update"


def test_service_with_no_ports_key_gets_one_created():
    data = load_compose("services:\n  api:\n    image: x\n")
    data, change = apply_port_mapping(data, "api", 8000, "tcp", 8000)
    out = dump_compose(data)
    assert "8000:8000" in out
    assert change.action == "add"


def test_round_trip_reparses_appended_entry_as_string_not_mapping():
    data = load_compose('services:\n  api:\n    ports:\n      - "9000:7000"\n')
    data, _ = apply_port_mapping(data, "api", 8000, "tcp", 8000)
    out = dump_compose(data)
    reparsed = load_compose(out)
    entry = reparsed["services"]["api"]["ports"][1]
    assert isinstance(entry, str)
    assert entry == "8000:8000"
