"""Administration endpoints: reading and trimming the audit log.

Admin-only (``api.deps.admin_only``). Two deliberate differences from
OpenSight, the reference implementation:

* the read FILTERS SERVER-SIDE. OpenSight's ``GET /api/admin/audit`` takes a
  limit and nothing else, and its Audit tab substring-matches a 200-row window
  in the browser - which silently cannot find anything older than those 200
  rows. Here ``action`` and ``user_id`` are SQL, and both columns are indexed.
* there is a PURGE. OpenSight has none: its docs tell operators to write their
  own DELETE. An audit table with no retention story is a table that grows
  until somebody notices.

Vertical 4d will add the rest of the admin surface (stats, jobs, storage) to
this router and put a page in front of it.
"""

from fastapi import APIRouter, Depends, Query

from api.audit import ACTIONS, AUDIT_PURGE, audit
from api.deps import admin_only
from api.store import AUDIT_DEFAULT_LIMIT, AUDIT_MAX_LIMIT, list_audit, purge_audit

router = APIRouter(prefix="/admin", tags=["admin"])

# A purge must name its window, so nobody empties the log by posting an empty
# form; ten years is past any plausible retention policy.
MAX_PURGE_DAYS = 3650


@router.get("/audit")
def read_audit(
    limit: int = Query(AUDIT_DEFAULT_LIMIT, ge=1, le=AUDIT_MAX_LIMIT),
    action: str | None = None,
    user_id: str | None = None,
    admin: dict = Depends(admin_only),
):
    """Audit entries, newest first, filtered in SQL.

    ``action`` is one of the constants in ``api.audit``; ``user_id`` is an
    account id. Rows carry the actor's ``username`` themselves, so an entry
    still names who did it after that account is gone - and a row with no
    ``user_id`` at all (a failed login) still names the username that was
    tried, rather than rendering as "system".

    ``actions`` is the whole vocabulary, so the filter control is built from
    the server's list rather than a copy of it kept in the frontend - the same
    reason the actions are named constants in the first place.
    """
    return {
        "entries": list_audit(limit=limit, action=action, user_id=user_id),
        "actions": list(ACTIONS),
    }


@router.post("/maintenance/purge-audit")
def purge_audit_entries(
    days: int = Query(..., ge=1, le=MAX_PURGE_DAYS),
    admin: dict = Depends(admin_only),
):
    """Delete audit entries older than ``days`` days; returns how many went.

    The purge is itself audited (after the delete, so its own row survives it).
    """
    deleted = purge_audit(days)
    audit(AUDIT_PURGE, user=admin, entity="audit",
          detail=f"older than {days} days: {deleted} removed")
    return {"deleted": deleted, "days": days}
