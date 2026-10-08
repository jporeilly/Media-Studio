"""The audit log (porting vertical 4a): the write helper and its deliberate
never-raises contract, the vocabulary, the admin read and purge endpoints, and
the two anti-rot guards that keep coverage honest as routes are added.

``AUDIT_EXEMPT`` below is the ONLY way a mutating endpoint may record nothing,
and every entry carries the reason. Keep it small: at this scale row volume is
not a problem (there is a purge endpoint), so the bar for exempting is "a row
here would be actively meaningless", not "this route is chatty".
"""

import ast
import inspect
import sqlite3
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import audit as audit_module
from api import store
from api.audit import audit
from api.store import list_audit
from utils.config import config

ROOT = Path(__file__).resolve().parent.parent
ROUTERS_DIR = ROOT / "api" / "routers"

PASSWORD = "Audit-pass-12345"

# Mutating endpoints that deliberately record nothing, by dotted name, each with
# the reason. Both are POSTs only because they call the model; neither records a
# user-visible change. Note what is NOT claimed here: both can still write
# project.json, because opening the inner engine project materialises it and
# refreshes slide_count (services/slides.py ``_open`` / ``_reconcile``). So does
# the plain GET of the slide list, which is correctly unaudited - that
# bookkeeping is not an audited event.
AUDIT_EXEMPT: dict[str, str] = {
    "api.routers.ai.ai_analyze": (
        "Scores the deck for video (text density, notes coverage, visuals) and "
        "records no user-visible change: no note, override or review is saved. "
        "It can materialise the engine project and refresh slide_count on first "
        "open (services/slides.py _open / _reconcile), exactly as the unaudited "
        "GET of the slide list does. The row would say somebody read a score."
    ),
    "api.routers.ai.ai_enhance_one": (
        "Returns a rewrite of one slide as a PROPOSAL the editor shows with "
        "Revert; nothing the user typed or chose is saved. Accepting it is a "
        "PATCH of the slide, which IS audited (slides.update), so a row here "
        "would log a change that may never be made. Like ai_analyze it can "
        "materialise the engine project on first open, as the unaudited GET of "
        "the slide list does."
    ),
    "api.routers.capture.freeze": (
        "Takes throwaway pictures of each monitor for the region overlay to draw "
        "over; nothing is kept (the page deletes them once they have served). What "
        "the user then captures is audited where it is made: capture.still for a "
        "still, recording.start for a recording."
    ),
    "api.routers.capture.forget_frozen": (
        "Deletes those throwaway overlay pictures once they have served: "
        "housekeeping of files no user made or named, with no record to change."
    ),
    "api.routers.recordings.put_chunk": (
        "One chunk of a recording in progress, every five seconds - 720 rows an "
        "hour of one recording. The recording's start, its save and its discard "
        "are audited (recording.start / finish / discard), which is the event."
    ),
    "api.routers.recordings.heartbeat": (
        "The recorder saying it is still recording (paused or between chunks), "
        "every five seconds, so the Projects page cannot save or discard a "
        "recording still being made. It changes nothing a user made or named; "
        "the recording's start, save and discard are the audited events."
    ),
    "api.routers.recordings.release": (
        "The recorder letting go of a recording it could not finish (a failure, "
        "a full disk, the cap), so the Projects page offers it at once instead of "
        "twenty seconds later. Nothing is made, named or removed; the recording's "
        "start and its later save or discard are the audited events."
    ),
}

# Deliberately NOT exempt, recorded here so the decision is visible next to the
# ones that are:
#   PATCH /api/projects/{pid}/slides/{index} (api.routers.slides.update_slide)
#   fires on every note save, so it is the busiest mutating route. It is still
#   audited: it is the route that changes what the narration SAYS, it fires on
#   a deliberate Save / dropdown / blur rather than per keystroke, and the
#   purge endpoint exists for volume. An audit log that cannot answer "who
#   changed this deck's script" is not worth keeping.

AUDIT_HINT = """
Record the action from the endpoint:

    from api.audit import PROJECT_IMPORT, audit
    ...
    audit(PROJECT_IMPORT, user=user, entity="project", entity_id=pid, detail="...")

Add a new named constant to api/audit.py (and to its ACTIONS tuple) if the
action is not in the vocabulary yet - never an inline string, which is how
OpenSight ended up with both "update_settings" and "settings_update".

If the endpoint genuinely should record nothing, add its dotted name to
AUDIT_EXEMPT at the top of tests/test_audit.py WITH THE REASON.
"""


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path / "auth")
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "auth" / "media_studio.db")
    monkeypatch.setattr(config, "_config", {})
    monkeypatch.setattr(config, "save", lambda: None)
    store.init_db()
    return store.DB_PATH


@pytest.fixture
def app():
    from api.app import app as fastapi_app

    with TestClient(fastapi_app):
        seeded = store.authenticate("admin", "admin")
        store.change_password(seeded["id"], "admin", must_change=False)
        yield fastapi_app


def _signed_in(app, username: str, password: str) -> TestClient:
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200
    return c


def _rows() -> list[dict]:
    return store.list_audit(limit=store.AUDIT_MAX_LIMIT)


def _actions() -> list[str]:
    return [row["action"] for row in _rows()]


# ── the write helper ──────────────────────────────────────────────────────────

def test_audit_records_the_actor_from_the_user_dict():
    user = store.create_user("jane", PASSWORD, "Jane Editor")
    audit(audit_module.PROJECT_IMPORT, user=user, entity="project", entity_id="abc123", detail="deck: a.pptx")

    (row,) = _rows()
    assert row["user_id"] == user["id"]
    assert row["username"] == "Jane Editor"  # the display name, denormalised
    assert row["action"] == "project.import"
    assert (row["entity"], row["entity_id"], row["detail"]) == ("project", "abc123", "deck: a.pptx")
    assert row["created_at"]


def test_audit_falls_back_to_the_username_when_there_is_no_display_name():
    user = store.create_user("terse", PASSWORD, "   ")
    audit(audit_module.AUTH_LOGIN, user=user, entity="auth")
    assert _rows()[0]["username"] == "terse"


def test_audit_with_a_username_alone_records_the_attempt_with_no_user_id():
    """A failed login has no session to take an actor from - the username that
    was tried is the whole point of the row, and the reason ``username`` is a
    column of its own rather than something to parse out of free text."""
    audit(audit_module.AUTH_LOGIN_FAILED, username="mallory", entity="auth")

    (row,) = _rows()
    assert row["user_id"] is None
    assert row["username"] == "mallory"
    assert row["action"] == "auth.login_failed"


def test_audit_stores_null_rather_than_failing_for_a_user_that_no_longer_exists():
    audit(audit_module.USER_UPDATE, user={"id": "gone-for-good", "display_name": "Ghost"}, entity="user")

    (row,) = _rows()
    assert row["user_id"] is None, "a dangling id must not be written"
    assert row["username"] == "Ghost", "the denormalised name is what keeps the row readable"


def test_a_failing_audit_write_never_reaches_the_caller(monkeypatch):
    """Deliberate divergence from OpenSight: the audit log is an
    administrator's convenience, not a compliance control, so a locked or full
    database must not stop somebody importing a project."""
    warnings = []

    def explode(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "record_audit", explode)
    monkeypatch.setattr(audit_module, "log", type("L", (), {"warning": lambda self, *a: warnings.append(a)})())

    assert audit(audit_module.PROJECT_DELETE, username="jane", entity="project") is None
    assert len(warnings) == 1 and "database is locked" in str(warnings[0])


def test_a_malformed_actor_does_not_raise_either(monkeypatch):
    """"Never raises" has to cover a bad ``user`` too, not only a bad write."""
    warnings = []
    monkeypatch.setattr(audit_module, "log", type("L", (), {"warning": lambda self, *a: warnings.append(a)})())

    assert audit(audit_module.PROJECT_IMPORT, user="not a user dict", entity="project") is None
    assert len(warnings) == 1
    assert _rows() == [], "nothing half-written"


def test_a_failing_audit_write_does_not_fail_the_request(app, monkeypatch):
    """The same contract at the seam that matters: through the API."""
    def explode(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(store, "record_audit", explode)
    admin = _signed_in(app, "admin", "admin")
    assert admin.post("/api/users", json={"username": "kit", "password": PASSWORD, "display_name": "Kit"}).status_code == 201
    assert store.authenticate("kit", PASSWORD) is not None, "the account was still created"


def test_changed_keys_lists_names_only():
    assert audit_module.changed_keys({"min_length": 20, "require_symbol": True}) == "min_length, require_symbol"
    assert audit_module.changed_keys({}) == "(nothing)"


# ── the vocabulary ────────────────────────────────────────────────────────────

def test_every_action_is_dotted_snake_case_and_unique():
    """``area.verb``, lower snake, no duplicates - so a filter on one action
    can never mean two things (OpenSight's ``update_settings`` vs
    ``settings_update``)."""
    import re

    assert len(set(audit_module.ACTIONS)) == len(audit_module.ACTIONS), "duplicate action"
    for action in audit_module.ACTIONS:
        assert re.fullmatch(r"[a-z]+\.[a-z][a-z_]*", action), action


def test_every_named_constant_is_in_the_actions_tuple():
    named = {
        value for name, value in vars(audit_module).items()
        if name.isupper() and isinstance(value, str) and "." in value
    }
    assert named == set(audit_module.ACTIONS), (
        "a constant was added without adding it to ACTIONS (or vice versa): "
        f"{named ^ set(audit_module.ACTIONS)}"
    )


def _audit_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``audit(...)`` call in a parsed tree, matched by SYNTAX.

    Never by substring: ``"audit(" in source`` is true of ``list_audit(...)``,
    ``record_audit(...)``, ``purge_audit(...)`` and even a commented-out call,
    so a coverage guard written that way fails OPEN - the next admin route that
    merely READS the log would pass it while recording nothing. The parser
    cannot be fooled that way (and ignores comments outright).
    """
    return [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "audit"
    ]


def test_routers_only_audit_with_named_constants_and_known_entities():
    """No inline action strings anywhere: a typo must be an ImportError, not a
    row nobody can ever filter for."""
    problems: list[str] = []
    for path in sorted(ROUTERS_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in _audit_calls(tree):
            where = f"{path.name}:{node.lineno}"
            first = node.args[0] if node.args else None
            if not isinstance(first, ast.Name) or getattr(audit_module, first.id, None) not in audit_module.ACTIONS:
                problems.append(f"{where}: the action must be a named constant from api/audit.py")
            for keyword in node.keywords:
                if keyword.arg == "entity" and isinstance(keyword.value, ast.Constant):
                    if keyword.value.value not in audit_module.ENTITIES:
                        problems.append(f"{where}: entity {keyword.value.value!r} is not in api.audit.ENTITIES")
    assert problems == [], "\n".join(problems)


# ── coverage of the mutating endpoints ────────────────────────────────────────

def _mutating(routes):
    return [r for r in routes if r.method not in ("GET", "HEAD", "OPTIONS")]


def _records_audit(route) -> bool:
    """The guard's own predicate: the endpoint really calls ``audit(...)``, or
    it is deliberately exempt. Parsed, not substring-matched - see
    ``_audit_calls``."""
    if route.name in AUDIT_EXEMPT:
        return True
    source = textwrap.dedent(inspect.getsource(route.endpoint))
    return bool(_audit_calls(ast.parse(source)))


def test_every_mutating_endpoint_records_an_audit_entry(routes):
    """The anti-rot guard: hand-written audit calls rot the moment somebody
    adds a route, so the app's own route table is the checklist."""
    silent = [str(r) for r in _mutating(routes) if not _records_audit(r)]
    assert silent == [], (
        "these mutating endpoints record nothing in the audit log:\n  "
        + "\n  ".join(silent) + "\n" + AUDIT_HINT
    )


def test_the_exempt_list_names_real_endpoints_and_says_why(routes):
    """The exempt list must rot too: a name that no longer exists, or one that
    has quietly started auditing, has to be removed."""
    live = {r.name for r in _mutating(routes)}
    unknown = sorted(set(AUDIT_EXEMPT) - live)
    assert unknown == [], f"AUDIT_EXEMPT names endpoints that are not mutating routes any more: {unknown}"
    thin = [name for name, reason in AUDIT_EXEMPT.items() if len(reason.split()) < 8]
    assert thin == [], f"every AUDIT_EXEMPT entry needs a written reason: {thin}"


def test_the_guard_would_catch_a_new_unaudited_route(routes):
    """Proof the guard bites: a stand-in endpoint with no audit call fails the
    very predicate every real route passes."""
    def brand_new_endpoint(user: dict):
        return {"ok": True}

    route_info = type(routes[0])  # the RouteInfo the conftest builds
    assert _records_audit(route_info("POST", "/api/brand-new", brand_new_endpoint)) is False
    from api.routers.projects import import_project

    assert _records_audit(route_info("POST", "/api/projects/import", import_project)) is True


def test_the_guard_is_not_fooled_by_a_name_that_merely_contains_audit(routes):
    """The fail-open a substring check would have had: an admin route that only
    READS the log calls ``list_audit(...)``, whose text contains "audit(" - and
    so does a call someone has commented out. Both must still count as silent."""
    def reads_the_log_but_records_nothing(user: dict):
        # audit(AUTH_LOGIN, user=user)  <- commented out, so it records nothing
        return {"entries": list_audit(limit=5)}

    route_info = type(routes[0])
    assert _records_audit(route_info("POST", "/api/admin/reads", reads_the_log_but_records_nothing)) is False


# ── what the endpoints actually record ────────────────────────────────────────

def test_login_logout_and_password_change_are_recorded(app):
    TestClient(app).post("/api/auth/login", json={"username": "admin", "password": "wrong"})
    admin = _signed_in(app, "admin", "admin")
    admin.post("/api/auth/change-password", json={"current_password": "admin", "new_password": PASSWORD})
    admin.post("/api/auth/logout")

    assert _actions() == ["auth.logout", "auth.password_change", "auth.login", "auth.login_failed"]
    failed = [r for r in _rows() if r["action"] == "auth.login_failed"][0]
    assert failed["user_id"] is None and failed["username"] == "admin", (
        "the username that was tried is recorded even though there is no session"
    )


def test_account_changes_are_recorded_without_any_password(app):
    admin = _signed_in(app, "admin", "admin")
    created = admin.post("/api/users", json={"username": "sam", "password": PASSWORD, "display_name": "Sam"}).json()
    admin.patch(f"/api/users/{created['id']}", json={"display_name": "Samantha"})
    admin.post(f"/api/users/{created['id']}/reset-password", json={"new_password": "Temp-pass-98765"})

    assert _actions() == ["user.reset_password", "user.update", "user.create", "auth.login"]
    for row in _rows():
        assert PASSWORD not in (row["detail"] or "") and "Temp-pass-98765" not in (row["detail"] or "")
    assert [r["detail"] for r in _rows() if r["action"] == "user.update"] == ["sam: display_name"]


def test_settings_audits_record_key_names_not_values(app):
    admin = _signed_in(app, "admin", "admin")
    r = admin.put("/api/settings/password-policy", json={
        "min_length": 20, "require_upper": True, "require_digit": True,
        "require_symbol": True, "forbid_username": True, "forbid_common": True,
    })
    assert r.status_code == 200, r.text
    r = admin.put("/api/settings/studio", json={"tts_provider": "edge_tts", "watermark_text": "ACME internal"})
    assert r.status_code == 200, r.text

    policy = [row for row in _rows() if row["action"] == "settings.password_policy"][0]
    assert policy["detail"] == (
        "forbid_common, forbid_username, min_length, require_digit, require_symbol, require_upper"
    )
    assert "20" not in policy["detail"], "a settings audit never carries values"
    studio = [row for row in _rows() if row["action"] == "settings.update"][0]
    assert studio["detail"] == "tts_provider, watermark_text"
    assert "edge_tts" not in studio["detail"] and "ACME internal" not in studio["detail"]


def test_a_project_import_and_delete_are_recorded_against_the_project(app, tmp_path, monkeypatch):
    from services import projects as project_store

    monkeypatch.setattr(project_store, "PROJECTS_DIR", tmp_path / "projects")
    admin = _signed_in(app, "admin", "admin")
    created = admin.post("/api/projects/import", files={"file": ("deck.pptx", b"not-a-real-deck", "application/octet-stream")})
    assert created.status_code == 200, created.text
    pid = created.json()["id"]
    assert admin.delete(f"/api/projects/{pid}").status_code == 204

    imported = [row for row in _rows() if row["action"] == "project.import"][0]
    assert imported["entity"] == "project" and imported["entity_id"] == pid
    assert imported["detail"] == "deck: deck.pptx"
    assert [r["entity_id"] for r in _rows() if r["action"] == "project.delete"] == [pid]


# ── the read endpoint ─────────────────────────────────────────────────────────

def test_read_is_newest_first_and_admin_only(app):
    admin = _signed_in(app, "admin", "admin")
    editor = store.create_user("ed", PASSWORD, "Ed", role="editor", must_change_password=False)
    editor_client = _signed_in(app, "ed", PASSWORD)

    r = admin.get("/api/admin/audit")
    assert r.status_code == 200
    # The vocabulary travels with the payload, so the filter control is built
    # from the server's list rather than a copy kept in the frontend.
    assert r.json()["actions"] == list(audit_module.ACTIONS)
    entries = r.json()["entries"]
    assert [e["action"] for e in entries] == ["auth.login", "auth.login"]
    assert entries[0]["created_at"] >= entries[1]["created_at"]
    assert {"id", "user_id", "username", "action", "entity", "entity_id", "detail", "created_at"} == set(entries[0])

    assert editor_client.get("/api/admin/audit").status_code == 403
    assert TestClient(app).get("/api/admin/audit").status_code == 401
    assert editor["id"]


def test_read_filters_on_action_and_user_id_in_sql(app):
    admin = _signed_in(app, "admin", "admin")
    admin_id = admin.get("/api/auth/me").json()["user"]["id"]
    store.create_user("ed", PASSWORD, "Ed", role="editor", must_change_password=False)
    ed = _signed_in(app, "ed", PASSWORD)
    ed.post("/api/auth/change-password", json={"current_password": PASSWORD, "new_password": "Another-pass-123"})
    TestClient(app).post("/api/auth/login", json={"username": "nobody", "password": "x"})

    logins = admin.get("/api/admin/audit?action=auth.login").json()["entries"]
    assert [e["action"] for e in logins] == ["auth.login", "auth.login"]

    mine = admin.get(f"/api/admin/audit?user_id={admin_id}").json()["entries"]
    assert [e["action"] for e in mine] == ["auth.login"] and mine[0]["user_id"] == admin_id

    failed = admin.get("/api/admin/audit?action=auth.login_failed").json()["entries"]
    assert len(failed) == 1 and failed[0]["user_id"] is None and failed[0]["username"] == "nobody"


def test_read_limit_defaults_to_100_and_is_capped_at_1000(app):
    admin = _signed_in(app, "admin", "admin")
    for i in range(105):
        audit(audit_module.SLIDES_UPDATE, username=f"u{i}", entity="project", entity_id="p")

    assert len(admin.get("/api/admin/audit").json()["entries"]) == store.AUDIT_DEFAULT_LIMIT
    assert len(admin.get("/api/admin/audit?limit=5").json()["entries"]) == 5
    assert len(admin.get("/api/admin/audit?limit=1000").json()["entries"]) == 106
    assert admin.get("/api/admin/audit?limit=1001").status_code == 422
    assert admin.get("/api/admin/audit?limit=0").status_code == 422
    # And the store clamps on its own, whatever a future caller passes.
    assert len(store.list_audit(limit=10_000)) == 106
    assert len(store.list_audit(limit="nonsense")) == store.AUDIT_DEFAULT_LIMIT


# ── the purge ─────────────────────────────────────────────────────────────────

def _age_all_rows(days: int) -> None:
    """Backdate every existing row by ``days`` days (the helper stamps now)."""
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(store.DB_PATH))
    conn.execute("UPDATE audit_log SET created_at = ?", (stamp,))
    conn.commit()
    conn.close()


def test_purge_removes_only_entries_older_than_the_window_and_audits_itself(app):
    admin = _signed_in(app, "admin", "admin")
    for i in range(3):
        audit(audit_module.SLIDES_RENDER, username=f"old{i}", entity="project", entity_id="p")
    _age_all_rows(90)
    audit(audit_module.SLIDES_RENDER, username="fresh", entity="project", entity_id="p")

    r = admin.post("/api/admin/maintenance/purge-audit?days=30")
    assert r.status_code == 200, r.text
    assert r.json() == {"deleted": 4, "days": 30}  # 3 old renders + the login

    remaining = _actions()
    assert remaining == ["audit.purge", "slides.render"], remaining
    assert _rows()[0]["detail"] == "older than 30 days: 4 removed"


def test_purge_is_admin_only_and_needs_a_window(app):
    admin = _signed_in(app, "admin", "admin")
    store.create_user("ed", PASSWORD, "Ed", role="editor", must_change_password=False)
    ed = _signed_in(app, "ed", PASSWORD)

    assert ed.post("/api/admin/maintenance/purge-audit?days=30").status_code == 403
    assert TestClient(app).post("/api/admin/maintenance/purge-audit?days=30").status_code == 401
    assert admin.post("/api/admin/maintenance/purge-audit").status_code == 422, "days is required"
    assert admin.post("/api/admin/maintenance/purge-audit?days=0").status_code == 422
    assert "audit.purge" not in _actions(), "a refused purge records nothing"


# ── the schema ────────────────────────────────────────────────────────────────

def test_init_db_creates_the_table_and_its_three_indexes_and_is_idempotent(isolated_db):
    store.init_db()  # a second run must not fail

    conn = sqlite3.connect(str(isolated_db))
    columns = {row[1]: row[2] for row in conn.execute("PRAGMA table_info(audit_log)")}
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(audit_log)")}
    conn.close()

    assert columns == {
        "id": "INTEGER", "user_id": "TEXT", "username": "TEXT", "action": "TEXT",
        "entity": "TEXT", "entity_id": "TEXT", "detail": "TEXT", "created_at": "TEXT",
    }
    assert {"idx_audit_log_created_at", "idx_audit_log_action", "idx_audit_log_user_id"} <= indexes


def test_an_older_database_without_the_table_grows_it_on_the_next_start(isolated_db):
    """The migration idiom: an install from before the audit log just gains the
    table on its next boot, with its users intact."""
    conn = sqlite3.connect(str(isolated_db))
    conn.execute("DROP TABLE audit_log")
    conn.commit()
    conn.close()
    store.create_user("before", PASSWORD, "Before")

    store.init_db()
    audit(audit_module.AUTH_LOGIN, username="before", entity="auth")
    assert _actions() == ["auth.login"]
    assert [u["username"] for u in store.list_users()] == ["before"]
