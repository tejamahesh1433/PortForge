"""Tests for the agent enrollment/registration workflow."""
import uuid
from datetime import timedelta

import pytest

from app.repositories.agent_repository import AgentRepository
from app.security.tokens import hash_token, tokens_match
from app.services import enrollment_service


def _enroll(db, raw_token, host_id=None):
    return enrollment_service.enroll_host(
        db,
        raw_enrollment_token=raw_token,
        host_id=host_id or uuid.uuid4(),
        hostname="test-host",
        operating_system="windows",
        os_version="10",
        architecture="x86_64",
        agent_version="1.0.0",
        docker_available=True,
    )


def test_valid_registration_succeeds(db_session):
    minted = enrollment_service.mint_enrollment_token(db_session, label="test")
    result = _enroll(db_session, minted.raw_token)

    assert result.host_id is not None
    assert result.agent_token  # a real, non-empty token was issued


def test_enrollment_token_only_hash_is_stored(db_session):
    minted = enrollment_service.mint_enrollment_token(db_session)
    repo = AgentRepository(db_session)
    token_row = repo.get_enrollment_token_by_hash(hash_token(minted.raw_token))
    assert token_row is not None
    assert token_row.token_hash != minted.raw_token  # never stored raw


def test_agent_token_only_hash_is_stored(db_session):
    minted = enrollment_service.mint_enrollment_token(db_session)
    result = _enroll(db_session, minted.raw_token)

    repo = AgentRepository(db_session)
    credential = repo.get_active_credential_for_host(result.host_id)
    assert credential is not None
    assert credential.token_hash != result.agent_token
    assert tokens_match(result.agent_token, credential.token_hash)


def test_invalid_enrollment_token_rejected(db_session):
    with pytest.raises(enrollment_service.InvalidEnrollmentTokenError):
        _enroll(db_session, "this-token-was-never-minted")


def test_expired_enrollment_token_rejected(db_session):
    minted = enrollment_service.mint_enrollment_token(db_session, ttl=timedelta(seconds=-1))
    with pytest.raises(enrollment_service.EnrollmentTokenExpiredError):
        _enroll(db_session, minted.raw_token)


def test_consumed_enrollment_token_cannot_be_replayed(db_session):
    minted = enrollment_service.mint_enrollment_token(db_session)
    _enroll(db_session, minted.raw_token, host_id=uuid.uuid4())

    with pytest.raises(enrollment_service.EnrollmentTokenConsumedError):
        _enroll(db_session, minted.raw_token, host_id=uuid.uuid4())


def test_duplicate_registration_of_same_host_reissues_credential(db_session):
    """Re-enrolling the same host_id (e.g. after a local data wipe) with a
    fresh enrollment token must succeed and revoke the old credential --
    not create two simultaneously-valid credentials for one host.
    """
    host_id = uuid.uuid4()
    first_token = enrollment_service.mint_enrollment_token(db_session)
    first_result = _enroll(db_session, first_token.raw_token, host_id=host_id)

    second_token = enrollment_service.mint_enrollment_token(db_session)
    second_result = _enroll(db_session, second_token.raw_token, host_id=host_id)

    repo = AgentRepository(db_session)
    active = repo.get_active_credential_for_host(host_id)
    assert active is not None
    assert tokens_match(second_result.agent_token, active.token_hash)
    assert not tokens_match(first_result.agent_token, active.token_hash)


def test_never_expiring_token_when_ttl_none(db_session):
    minted = enrollment_service.mint_enrollment_token(db_session, ttl=None)
    assert minted.expires_at is None
    result = _enroll(db_session, minted.raw_token)
    assert result.host_id is not None
