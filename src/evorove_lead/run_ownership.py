"""One renewable tenant lease, shared by API, CLI, research and schedulers.

Lease acquisition and fencing use the existing 0011 tables. No network call is
made under a database lock. An already dispatched request cannot be recalled.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from uuid import uuid4

from sqlalchemy import event, select, update, delete, func
from sqlalchemy.orm import Session

CURRENT = ContextVar("evorove_run_owner", default=None)
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30


class OwnershipLost(RuntimeError):
    pass


class RunBusy(RuntimeError):
    pass


def aware(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def check_ownership():
    owner = CURRENT.get()
    if owner is not None:
        owner.check()


def guarded_call(call, *args, **kwargs):
    check_ownership()
    value = call(*args, **kwargs)
    check_ownership()
    return value


@event.listens_for(Session, "after_begin")
def _fence_owned_transaction(session, transaction, connection):
    """Hold the lease row until commit: takeover cannot race a managed DB write.

    Only sessions inside an owned run, on that run's database, are affected.
    Core SQL in LeaseStore deliberately does not enter this hook.
    """
    owner = CURRENT.get()
    if owner is not None and connection.engine is owner.store.engine:
        owner.store.fence(connection, owner.business_id, owner.run_id)


class LeaseStore:
    def __init__(self, engine, *, clock=None, seconds=LEASE_SECONDS):
        self.engine, self.clock, self.seconds = engine, clock, seconds

    def now(self, connection):
        if self.clock:
            return self.clock()
        if self.engine.dialect.name == "postgresql":
            return connection.scalar(select(func.clock_timestamp()))
        return datetime.now(timezone.utc)

    @staticmethod
    def tables():
        from evorove_lead.research_delivery import RunLeaseRow
        from evorove_lead.intelligence_store import ResearchDocumentRow
        from evorove_lead.sqlalchemy_models import SearchTargetRow
        return RunLeaseRow.__table__, ResearchDocumentRow.__table__, SearchTargetRow.__table__

    def insert(self, table):
        if self.engine.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        return insert(table)

    def document(self, c, tenant, kind, identifier, payload, now):
        _, docs, _ = self.tables()
        stmt = self.insert(docs).values(business_id=tenant, kind=kind, id=identifier, payload=payload, created_at=now)
        c.execute(stmt.on_conflict_do_update(index_elements=["business_id", "kind", "id"], set_={"payload": payload}))

    def _interrupt(self, c, tenant, now, *, except_id=None, reason="heartbeat_expired"):
        _, docs, targets = self.tables()
        rows = c.execute(select(docs).where(docs.c.business_id == tenant, docs.c.kind.in_(("run", "run_attempt")))).mappings()
        for row in rows:
            payload = dict(row["payload"])
            if row["id"] != except_id and payload.get("status") == "running":
                payload.update(status="interrupted", stop_reason=reason, finished_at=now.isoformat())
                self.document(c, tenant, row["kind"], row["id"], payload, now)
        c.execute(update(targets).where(targets.c.business_id == tenant, targets.c.last_status == "running").values(last_status="interrupted", last_run_at=now))

    def acquire(self, tenant, *, target=None, expected_last_run=None, compare_target=False):
        lease, docs, targets = self.tables()
        run_id = "attempt_" + uuid4().hex
        with self.engine.begin() as c:
            now = self.now(c)
            # Single conditional UPSERT is the atomic right to execute. Any
            # subsequent refusal rolls back the entire transaction.
            prior_id = c.execute(select(lease.c.run_id).where(lease.c.business_id == tenant)).scalar_one_or_none()
            stmt = self.insert(lease).values(business_id=tenant, run_id=run_id, expires_at=now + timedelta(seconds=self.seconds))
            stmt = stmt.on_conflict_do_update(index_elements=["business_id"], set_={"run_id": run_id, "expires_at": now + timedelta(seconds=self.seconds)}, where=lease.c.expires_at <= now)
            if c.execute(stmt.returning(lease.c.run_id)).scalar_one_or_none() != run_id:
                raise RunBusy("run_already_owned")
            previous = c.execute(select(docs.c.payload).where(docs.c.business_id == tenant, docs.c.kind == "run_attempt")).scalars().all()
            row = c.execute(select(targets).where(targets.c.business_id == tenant)).mappings().first()
            if row and row["last_status"] == "running" and not any(p.get("status") == "running" for p in previous):
                raise RunBusy("legacy_running_unverified")
            # Old research leases/runs did not heartbeat or fence writes. Their
            # processes must be stopped explicitly before adopting them.
            legacy = c.execute(select(docs.c.payload).where(docs.c.business_id == tenant, docs.c.kind == "run")).scalars().all()
            ids = {p["id"] for p in previous}
            if prior_id and prior_id not in ids:
                raise RunBusy("legacy_running_unverified")
            if any(p.get("status") == "running" and p.get("id") not in ids for p in legacy):
                raise RunBusy("legacy_running_unverified")
            if compare_target and row and (aware(row["last_run_at"]) if row["last_run_at"] else None) != expected_last_run:
                raise RunBusy("target_already_ran")
            self._interrupt(c, tenant, now)
            state = {"id": run_id, "status": "running", "started_at": now.isoformat(), "heartbeat_at": now.isoformat(), "previous_attempt_ids": [p["id"] for p in previous if p.get("status") == "running"]}
            self.document(c, tenant, "run_attempt", run_id, state, now)
            if target is not None:
                values = {**target.__dict__, "last_status": "running"}
                stmt = self.insert(targets).values(**values)
                c.execute(stmt.on_conflict_do_update(index_elements=["business_id"], set_={k: v for k, v in values.items() if k not in {"business_id", "created_at"}}))
            elif row:
                c.execute(update(targets).where(targets.c.business_id == tenant).values(last_status="running"))
        return RunOwner(self, tenant, run_id)

    def fence(self, c, tenant, run_id):
        lease, _, _ = self.tables()
        row = c.execute(select(lease).where(lease.c.business_id == tenant).with_for_update()).mappings().first()
        if not row or row["run_id"] != run_id or aware(row["expires_at"]) <= self.now(c):
            raise OwnershipLost("run_ownership_lost")

    def heartbeat(self, tenant, run_id):
        lease, docs, _ = self.tables()
        with self.engine.begin() as c:
            now = self.now(c)
            result = c.execute(update(lease).where(lease.c.business_id == tenant, lease.c.run_id == run_id, lease.c.expires_at > now).values(expires_at=now + timedelta(seconds=self.seconds)))
            if result.rowcount != 1:
                raise OwnershipLost("run_ownership_lost")
            payload = dict(c.execute(select(docs.c.payload).where(docs.c.business_id == tenant, docs.c.kind == "run_attempt", docs.c.id == run_id)).scalar_one())
            payload["heartbeat_at"] = now.isoformat()
            self.document(c, tenant, "run_attempt", run_id, payload, now)

    def finish(self, tenant, run_id, status, reason="", cold=0):
        lease, docs, targets = self.tables()
        with self.engine.begin() as c:
            self.fence(c, tenant, run_id)
            now = self.now(c)
            payload = dict(c.execute(select(docs.c.payload).where(docs.c.business_id == tenant, docs.c.kind == "run_attempt", docs.c.id == run_id)).scalar_one())
            payload.update(status=status, stop_reason=reason, finished_at=now.isoformat(), cold=cold)
            self.document(c, tenant, "run_attempt", run_id, payload, now)
            run = c.execute(select(docs.c.payload).where(docs.c.business_id == tenant, docs.c.kind == "run", docs.c.id == run_id)).scalar_one_or_none()
            if run and run.get("status") == "running":
                self.document(c, tenant, "run", run_id, {**run, "status": status, "stop_reason": reason, "finished_at": now.isoformat()}, now)
            c.execute(update(targets).where(targets.c.business_id == tenant).values(last_status=status, last_cold=cold, last_run_at=now))
            self.document(c, tenant, "search_status", "latest", {"status": status, "stop_reason": reason, "attempt_id": run_id, "last_run_at": now.isoformat()}, now)
            c.execute(delete(lease).where(lease.c.business_id == tenant, lease.c.run_id == run_id))

    def describe(self, tenant):
        lease, docs, targets = self.tables()
        with self.engine.connect() as c:
            row = c.execute(select(lease).where(lease.c.business_id == tenant)).mappings().first()
            if row:
                attempt = c.execute(select(docs.c.payload).where(docs.c.business_id == tenant, docs.c.kind == "run_attempt", docs.c.id == row["run_id"])).scalar_one_or_none()
                if attempt:
                    return {"attempt_id": row["run_id"], "heartbeat_at": attempt["heartbeat_at"], "lease_expires_at": aware(row["expires_at"]).isoformat()}
            target = c.execute(select(targets.c.last_status).where(targets.c.business_id == tenant)).scalar_one_or_none()
            return {"stop_reason": "legacy_running_unverified"} if target == "running" else {}

    def recover(self, tenant, *, legacy_workers_stopped=False):
        """Mark interrupted and release. Does NOT execute or retry anything."""
        lease, docs, targets = self.tables()
        with self.engine.begin() as c:
            row = c.execute(select(lease).where(lease.c.business_id == tenant).with_for_update()).mappings().first()
            now = self.now(c)
            attempt = c.execute(select(docs.c.payload).where(docs.c.business_id == tenant, docs.c.kind == "run_attempt", docs.c.id == row["run_id"])).scalar_one_or_none() if row else None
            if row and aware(row["expires_at"]) > now and (attempt or not legacy_workers_stopped):
                return False
            target = c.execute(select(targets).where(targets.c.business_id == tenant).with_for_update()).mappings().first()
            if not row and (not target or target["last_status"] != "running"):
                return False
            if not attempt and not legacy_workers_stopped:
                return False
            reason = "legacy_workers_stopped" if not attempt else "heartbeat_expired"
            self._interrupt(c, tenant, now, reason=reason)
            self.document(c, tenant, "search_status", "latest", {"status": "interrupted", "stop_reason": reason, "attempt_id": row["run_id"] if row else None, "last_run_at": now.isoformat()}, now)
            if row:
                c.execute(delete(lease).where(lease.c.business_id == tenant, lease.c.run_id == row["run_id"]))
            return True


class RunOwner:
    def __init__(self, store, business_id, run_id):
        self.store, self.business_id, self.run_id = store, business_id, run_id
        self.lost = Event()
        self.done = False

    def check(self):
        if self.lost.is_set() or self.done:
            raise OwnershipLost("run_ownership_lost")
        with self.store.engine.begin() as c:
            self.store.fence(c, self.business_id, self.run_id)

    def heartbeat(self):
        self.store.heartbeat(self.business_id, self.run_id)

    def finish(self, status, reason="", cold=0):
        self.store.finish(self.business_id, self.run_id, status, reason, cold)
        self.done = True

    @contextmanager
    def activate(self, *, heartbeat=True):
        self.check()
        stop = Event()
        def renew():
            while not stop.wait(HEARTBEAT_SECONDS):
                try:
                    self.heartbeat()
                except Exception:
                    self.lost.set()
                    return
        worker = Thread(target=renew, name="lead-run-heartbeat", daemon=True) if heartbeat else None
        token = CURRENT.set(self)
        if worker:
            worker.start()
        try:
            yield self
        except BaseException as exc:
            if not self.done:
                try:
                    from evorove_lead.web_search import SearchError
                    status = "interrupted" if not isinstance(exc, Exception) else "budget_exhausted" if isinstance(exc, SearchError) and exc.code == "search_request_limit" else "failed"
                    reason = "cancelled" if not isinstance(exc, Exception) else exc.code if isinstance(exc, SearchError) else "dependency_failure"
                    self.finish(status, reason)
                except OwnershipLost:
                    pass
            raise
        finally:
            stop.set()
            # A delayed DB heartbeat is bounded by deployment DB timeouts; the
            # daemon cannot keep the process alive. Its token is always fenced.
            if worker:
                worker.join(timeout=1)
            CURRENT.reset(token)
