import uuid
import pytest
from pydantic import ValidationError

from app.schemas.allocation import AllocationRequestItem
from app.services.recommendation_service import find_available_port, CandidateSearchResult

def test_allocation_request_item_range_validation():
    # Valid ranges
    item = AllocationRequestItem(name="t1", purpose="api", requested_range="8000-8999")
    assert item.requested_range == "8000-8999"

    item = AllocationRequestItem(name="t2", purpose="api", requested_range="8127-8127")
    assert item.requested_range == "8127-8127"

    # Invalid ranges
    invalid_ranges = [
        "0-100",        # min_port < 1
        "8000-7000",    # min_port > max_port
        "65535-65536",  # max_port > 65535
        "abc",          # not MIN-MAX format
        "8000-",        # missing max_port
        "-9000",        # missing min_port
        "8000-abc",     # invalid integer
    ]

    for ir in invalid_ranges:
        with pytest.raises(ValidationError):
            AllocationRequestItem(name="tx", purpose="api", requested_range=ir)

    # preferred_port outside requested_range
    with pytest.raises(ValidationError) as exc_info:
        AllocationRequestItem(name="t3", purpose="api", preferred_port=9000, requested_range="8000-8999")
    assert "preferred_port 9000 is outside requested_range 8000-8999" in str(exc_info.value)
    
    # preferred_port inside requested_range
    item = AllocationRequestItem(name="t4", purpose="api", preferred_port=8127, requested_range="8000-8999")
    assert item.preferred_port == 8127
    assert item.requested_range == "8000-8999"

def test_find_available_port_with_requested_range(db_session):
    host_id = uuid.uuid4()
    
    # Normally "frontend" is 3000-3999. With range, we can force it elsewhere.
    result = find_available_port(db_session, host_id, "frontend", requested_range=(9000, 9005))
    assert result.port == 9000
    
    # Test exhaustion
    result = find_available_port(db_session, host_id, "api", requested_range=(8127, 8127), exclude_ports=frozenset([8127]))
    assert result.port is None
    
    # Test valid single port
    result = find_available_port(db_session, host_id, "api", requested_range=(8127, 8127))
    assert result.port == 8127
