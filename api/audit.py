"""The audit log: one vocabulary of actions, and the one call that writes them.

Every mutating endpoint records what happened (``tests/test_audit.py`` has a
guard that fails the build when a new one does not). Admins read the log at
``GET /api/admin/audit`` and trim it at
``POST /api/admin/maintenance/purge-audit`` (``api/routers/admin.py``).

**The vocabulary.** Actions are dotted ``area.verb`` and every one of them is a
named constant below. OpenSight - the reference implementation - uses bare
snake_case written inline at the call site, and it drifted: the same event is
``update_settings`` in one router and ``settings_update`` in another, so no
filter can find both. Naming them here means a typo is an ImportError rather
than a row nobody will ever find again. ``ACTIONS`` is the whole vocabulary and
``tests/test_audit.py`` checks that every ``audit(...)`` call in ``api/routers``
passes one of these names rather than a string literal.

**Entities.** The dotted prefix names the feature AREA the action belongs to
(``slides.update``, ``ai.translate``); the ``entity`` column says what the row
points at, which for both of those is the project. ``ENTITIES`` is that short
vocabulary, ``entity_id`` is the thing's id (a project id, a user id, a job id)
and ``detail`` is free text for a human.

**Secrets.** Never put a value in ``detail`` that would be embarrassing to read
back. A settings change records the KEY NAMES that changed and nothing else -
the password policy lives in settings, and so will anything else an admin can
configure.
"""

from api import store
from utils.logger import get_logger

log = get_logger("AUDIT")

# ── the vocabulary ───────────────────────────────────────────────────────────
# Authentication (``auth.login_failed`` is the one action with no user row
# behind it: the username that was tried is recorded, user_id stays NULL).
AUTH_LOGIN = "auth.login"
AUTH_LOGIN_FAILED = "auth.login_failed"
AUTH_LOGOUT = "auth.logout"
AUTH_PASSWORD_CHANGE = "auth.password_change"

# Accounts (admins acting on somebody's account).
USER_CREATE = "user.create"
USER_UPDATE = "user.update"
USER_RESET_PASSWORD = "user.reset_password"

# Studio settings (key names only - never the values).
SETTINGS_UPDATE = "settings.update"
SETTINGS_PASSWORD_POLICY = "settings.password_policy"

# Projects.
PROJECT_IMPORT = "project.import"
PROJECT_DELETE = "project.delete"
PROJECT_TRANSCRIBE = "project.transcribe"
PROJECT_TRANSCRIPT_EDIT = "project.transcript_edit"
# The per-sentence narration adjustment (offset / mute / voice / speed). Its own
# action rather than ``transcript_edit``: that one changes what is SAID, this one
# changes only when and how it is spoken, and an admin asking "who moved this
# sentence" should not have to read through every text save. The detail carries
# field names only - never the sentence.
PROJECT_TRANSCRIPT_TIMING = "project.transcript_timing"
PROJECT_GENERATE = "project.generate"
PROJECT_REVOICE = "project.revoice"
# The edit: which ranges of the source are kept (``services.edit``), stored or
# cleared. The detail carries the range count and the seconds removed - never
# the times, which would say where every cut is, and never any text.
PROJECT_EDIT = "project.edit"

# The slide editor.
SLIDES_UPDATE = "slides.update"
SLIDES_BULK_UPDATE = "slides.bulk_update"
SLIDES_RENDER = "slides.render"
SLIDES_UNDO = "slides.undo"
SLIDES_RESET = "slides.reset"

# The AI assistant.
AI_NOTES = "ai.notes"
AI_ENHANCE = "ai.enhance"
AI_QA = "ai.qa"
AI_TONE = "ai.tone"
AI_TRANSLATE = "ai.translate"
AI_PACING = "ai.pacing"
AI_QA_DOC = "ai.qa_doc"
AI_QA_FIX = "ai.qa_fix"

# Jobs and the server itself.
JOB_CANCEL = "job.cancel"
SYSTEM_UPDATE = "system.update"
SYSTEM_RESTART = "system.restart"
AUDIT_PURGE = "audit.purge"

ACTIONS: tuple[str, ...] = (
    AUTH_LOGIN, AUTH_LOGIN_FAILED, AUTH_LOGOUT, AUTH_PASSWORD_CHANGE,
    USER_CREATE, USER_UPDATE, USER_RESET_PASSWORD,
    SETTINGS_UPDATE, SETTINGS_PASSWORD_POLICY,
    PROJECT_IMPORT, PROJECT_DELETE, PROJECT_TRANSCRIBE, PROJECT_TRANSCRIPT_EDIT,
    PROJECT_TRANSCRIPT_TIMING, PROJECT_GENERATE, PROJECT_REVOICE, PROJECT_EDIT,
    SLIDES_UPDATE, SLIDES_BULK_UPDATE, SLIDES_RENDER, SLIDES_UNDO, SLIDES_RESET,
    AI_NOTES, AI_ENHANCE, AI_QA, AI_TONE, AI_TRANSLATE, AI_PACING, AI_QA_DOC, AI_QA_FIX,
    JOB_CANCEL, SYSTEM_UPDATE, SYSTEM_RESTART, AUDIT_PURGE,
)

# What the action was done to (the ``entity`` column).
ENTITIES: tuple[str, ...] = ("auth", "user", "settings", "project", "job", "system", "audit")


def audit(action: str, *, user: dict | None = None, username: str | None = None,
          entity: str | None = None, entity_id: str | None = None,
          detail: str | None = None) -> None:
    """Record one audit row. **This function never raises.**

    ``user`` is the actor from ``Depends(current_user)`` - it supplies both the
    user id and the name stored on the row. ``username`` alone is for the one
    case with no session to take a user from: a failed login, where the name
    that was tried is the whole point of the row.

    *Deliberate divergence from OpenSight, which has no try/except here:* a
    failed audit write is logged and dropped, never propagated. In this product
    the audit log is an administrator's convenience, not a compliance control -
    a locked, full or missing database must not stop someone importing a
    project or signing in. If you ever need auditing to be a hard requirement,
    that is a product decision to take deliberately (and to say so in the
    docs); do not turn it into one by deleting this except clause.
    """
    # Resolving the actor is inside the try as well: "never raises" has to hold
    # for a malformed ``user`` too, or the contract is only half a contract.
    try:
        user_id = None
        if user is not None:
            user_id = user.get("id")
            username = username or store.display_name_of(user)
        store.record_audit(action, user_id=user_id, username=username or None,
                           entity=entity, entity_id=entity_id, detail=detail)
    except Exception as exc:  # noqa: BLE001 - see the docstring: never fail the caller
        log.warning("Audit write failed (%s): %s", action, exc)


def changed_keys(changes: dict) -> str:
    """The key names of a settings change, comma-separated, for ``detail``.

    Values never go in the log: the password policy lives in settings, and this
    is the helper that keeps that rule impossible to forget at a call site.
    """
    return ", ".join(sorted(str(k) for k in changes)) or "(nothing)"
