"""Basic tests for Settings assembly and ORM relationships."""
import uuid
from datetime import datetime, timezone

from app.config import Settings
from app.models.host import Host
from app.models.port_observation import CurrentPortObservation
from app.models.reservation import CentralReservation


def test_settings_builds_database_url_from_parts():
    settings = Settings(
        database_url=None,
        db_host="myhost",
        db_host_port=12345,
        db_name="mydb",
        db_user="myuser",
        db_password="mypass",
    )
    url = settings.sqlalchemy_database_url
    assert "myhost" in url
    assert "12345" in url
    assert "mydb" in url
    assert "myuser" in url


def test_explicit_database_url_takes_precedence():
    settings = Settings(database_url="postgresql+psycopg://explicit/url")
    assert settings.sqlalchemy_database_url == "postgresql+psycopg://explicit/url"


def test_host_port_observations_relationship(db_session):
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    host = Host(id=host_id, hostname="h", operating_system="windows", first_seen=now, last_seen=now)
    db_session.add(host)
    db_session.commit()

    db_session.add(
        CurrentPortObservation(
            host_id=host_id,
            port=8000,
            protocol="tcp",
            bind_address="0.0.0.0",
            state="ACTIVE",
            source="process",
            first_seen=now,
            last_seen=now,
            observed_at=now,
            scan_id=uuid.uuid4(),
        )
    )
    db_session.commit()
    db_session.refresh(host)

    assert len(host.port_observations) == 1
    assert host.port_observations[0].port == 8000


def test_host_reservations_relationship(db_session):
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    host = Host(id=host_id, hostname="h", operating_system="windows", first_seen=now, last_seen=now)
    db_session.add(host)
    db_session.commit()

    db_session.add(CentralReservation(host_id=host_id, port=8003, protocol="tcp", project="deeptrace"))
    db_session.commit()
    db_session.refresh(host)

    assert len(host.reservations) == 1


def test_deleting_host_cascades_to_observations(db_session):
    host_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    host = Host(id=host_id, hostname="h", operating_system="windows", first_seen=now, last_seen=now)
    db_session.add(host)
    db_session.commit()

    db_session.add(
        CurrentPortObservation(
            host_id=host_id,
            port=8000,
            protocol="tcp",
            bind_address="0.0.0.0",
            state="ACTIVE",
            source="process",
            first_seen=now,
            last_seen=now,
            observed_at=now,
            scan_id=uuid.uuid4(),
        )
    )
    db_session.commit()

    db_session.delete(host)
    db_session.commit()

    from sqlalchemy import select

    remaining = db_session.execute(select(CurrentPortObservation).where(CurrentPortObservation.host_id == host_id)).scalars().all()
    assert remaining == []
