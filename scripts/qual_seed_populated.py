"""Seed + verify populated qual_populated DB after alembic upgrade head."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.allocation import Allocation
from app.models.host import Host
from app.models.reservation import CentralReservation


def main() -> None:
    engine = create_engine(get_settings().sqlalchemy_database_url)
    now = datetime.now(timezone.utc)
    with Session(engine) as db:
        hid = uuid.uuid4()
        db.add(
            Host(
                id=hid,
                hostname="seed-v13-host",
                operating_system="linux",
                agent_version="1.3.0",
                protocol_version=1,
                status="online",
                lifecycle_state="ACTIVE",
                first_seen=now,
                last_seen=now,
            )
        )
        db.flush()
        aid = uuid.uuid4()
        db.add(
            Allocation(
                id=aid,
                host_id=hid,
                project="seed-project",
                status="active",
                request_id="seed-req-1",
            )
        )
        db.flush()
        db.add(
            CentralReservation(
                id=uuid.uuid4(),
                host_id=hid,
                project="seed-project",
                purpose="api",
                protocol="tcp",
                port=18127,
                allocation_id=aid,
                request_name="api",
            )
        )
        db.commit()
        print("seeded", hid)

    with engine.connect() as c:
        row = c.execute(text("SELECT hostname, lifecycle_state, contract_version FROM hosts")).fetchone()
        print("host_row", tuple(row) if row else None)
        print("alloc_count", c.execute(text("SELECT count(*) FROM allocations")).scalar())
        print("res_count", c.execute(text("SELECT count(*) FROM central_reservations")).scalar())
        print("upgrade_table", c.execute(text("SELECT to_regclass('host_upgrades')")).scalar())
        assert row is not None and row[1] == "ACTIVE"
        assert row[2] is None
        print("POPULATED_MIGRATION=PASS")


if __name__ == "__main__":
    main()
