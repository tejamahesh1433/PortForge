import pytest
from portforge_agent.cli import _parse_request_flag

def test_parse_request_flag_range():
    # Valid syntax without range
    req, err = _parse_request_flag("web:frontend:tcp")
    assert err is None
    assert req == {"name": "web", "purpose": "frontend", "protocol": "tcp"}

    # Valid syntax with preferred_port
    req, err = _parse_request_flag("web:frontend:tcp:3000")
    assert err is None
    assert req == {"name": "web", "purpose": "frontend", "protocol": "tcp", "preferred_port": 3000}

    # Valid syntax with range (no preferred port)
    req, err = _parse_request_flag("web:frontend:tcp:8000-8999")
    assert err is None
    assert req == {"name": "web", "purpose": "frontend", "protocol": "tcp", "requested_range": "8000-8999"}

    # Valid syntax with preferred_port AND range
    req, err = _parse_request_flag("web:frontend:tcp:8000:8000-8999")
    assert err is None
    assert req == {"name": "web", "purpose": "frontend", "protocol": "tcp", "preferred_port": 8000, "requested_range": "8000-8999"}

    # Invalid range in 5th place
    req, err = _parse_request_flag("web:frontend:tcp:8000:abc")
    assert err is not None
    assert "range must contain '-'" in err

    # Invalid preferred_port outside range
    req, err = _parse_request_flag("web:frontend:tcp:9000:8000-8999")
    assert err is not None
    assert "preferred_port 9000 is outside requested_range 8000-8999" in err

    # Too many parameters
    req, err = _parse_request_flag("web:frontend:tcp:8000:8000-8999:extra")
    assert err is not None
    assert "expected name:purpose[:protocol[:preferred_port_or_range[:range]]]" in err
