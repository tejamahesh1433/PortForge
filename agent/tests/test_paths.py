from portforge_agent import paths, platform as pf


def test_windows_data_dir_uses_localappdata(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\someone\AppData\Local")

    result = paths.data_dir()
    assert str(result) == r"C:\Users\someone\AppData\Local\PortForge"


def test_windows_data_dir_falls_back_without_localappdata(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.WINDOWS)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: paths.Path("/home/someone")))

    result = paths.data_dir()
    assert result.name == "PortForge"
    assert "AppData" in result.parts and "Local" in result.parts


def test_macos_data_dir(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.MACOS)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: paths.Path("/Users/someone")))

    result = paths.data_dir()
    assert result.as_posix() == "/Users/someone/Library/Application Support/PortForge"


def test_linux_data_dir_uses_xdg_data_home(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    monkeypatch.setenv("XDG_DATA_HOME", "/home/someone/.data")

    result = paths.data_dir()
    assert result.as_posix() == "/home/someone/.data/portforge"


def test_linux_data_dir_falls_back_to_local_share(monkeypatch):
    monkeypatch.setattr(pf, "detect_os", lambda: pf.OperatingSystem.LINUX)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(paths.Path, "home", classmethod(lambda cls: paths.Path("/home/someone")))

    result = paths.data_dir()
    assert result.as_posix() == "/home/someone/.local/share/portforge"


def test_reservations_and_lock_paths_are_under_data_dir(monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: paths.Path("/tmp/pf"))
    assert paths.reservations_path() == paths.Path("/tmp/pf/reservations.json")
    assert paths.lock_path() == paths.Path("/tmp/pf/reservations.lock")


def test_config_path_candidates_order(monkeypatch):
    monkeypatch.setattr(paths, "data_dir", lambda: paths.Path("/tmp/pf"))
    candidates = paths.config_path_candidates()
    assert [c.name for c in candidates] == ["config.yml", "config.yaml", "config.json"]


def test_no_hardcoded_username_in_module_source():
    import inspect

    source = inspect.getsource(paths)
    # Guard against accidentally hardcoding this dev machine's username.
    assert "ntmke" not in source.lower()
