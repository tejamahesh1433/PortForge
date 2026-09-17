from portforge_agent import platform as pf


def test_detect_os_windows(monkeypatch):
    monkeypatch.setattr(pf._stdlib_platform, "system", lambda: "Windows")
    assert pf.detect_os() == pf.OperatingSystem.WINDOWS


def test_detect_os_macos(monkeypatch):
    monkeypatch.setattr(pf._stdlib_platform, "system", lambda: "Darwin")
    assert pf.detect_os() == pf.OperatingSystem.MACOS


def test_detect_os_linux(monkeypatch):
    monkeypatch.setattr(pf._stdlib_platform, "system", lambda: "Linux")
    assert pf.detect_os() == pf.OperatingSystem.LINUX


def test_detect_os_unknown(monkeypatch):
    monkeypatch.setattr(pf._stdlib_platform, "system", lambda: "PlanNine")
    assert pf.detect_os() == pf.OperatingSystem.UNKNOWN


def test_get_hostname_returns_nonempty_string():
    hostname = pf.get_hostname()
    assert isinstance(hostname, str)
    assert hostname


def test_get_hostname_survives_socket_error(monkeypatch):
    def _raise():
        raise OSError("no hostname")

    monkeypatch.setattr(pf.socket, "gethostname", _raise)
    assert pf.get_hostname() == "unknown-host"


def test_get_host_id_is_stable_across_calls(tmp_path, monkeypatch):
    # Phase 5: get_host_id() returns a persisted UUID (see identity.py),
    # not the hostname -- but it must still be a stable, opaque identifier
    # across repeated calls within a process.
    monkeypatch.setattr("portforge_agent.paths.data_dir", lambda: tmp_path)
    first = pf.get_host_id()
    second = pf.get_host_id()
    assert first == second
    assert first  # non-empty


def test_get_host_id_is_a_valid_uuid(tmp_path, monkeypatch):
    import uuid

    monkeypatch.setattr("portforge_agent.paths.data_dir", lambda: tmp_path)
    host_id = pf.get_host_id()
    uuid.UUID(host_id)  # raises ValueError if not a valid UUID string
