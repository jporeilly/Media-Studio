# Porting spec — enterprise administration (vertical 4)

Read-only survey, 2026-09-14, of Slide Studio Enterprise's "Enterprise Administration"
(`C:\Projects\slidestudio_enterprise\gui\enterprise\`) against Media Studio and OpenSight.

## Headline

SlideStudio's enterprise layer is ONE real feature (per-user filesystem isolation), one
real-ish feature (an admin dashboard over a metrics table) and SIX schemas with UIs but no
producers. `gui/enterprise/database.py:54-186` defines approvals, notifications, branding,
shared templates, scheduled jobs and job history; the live `data/enterprise.db` holds 156
metrics rows (all login/logout/password) and ZERO rows in every other table. Its own
`TASKS.md:167-191` lists them as "documented but not wired". Port shapes from OpenSight
instead, where they exist and are exercised.

## What SlideStudio actually has

1. **Per-user isolation** — `gui/enterprise/session.py:32-118`: `data/sessions/<username>/`
   with uploads/projects/temp/finished/cache/music/transitions/voices/videos; the engine is
   namespaced only by the `projects_base` passed in (`core/project_manager.py:372-374`).
   The per-user `SessionConfig` (`enterprise_config.py:22-93`) has ZERO callers — everyone
   shares the global `data/config.json`.
2. **Approval workflow** — states pending → approved|rejected (`database.py:396-435`);
   reviewer hard-coded "admin" (`features.py:163,173`); `submit_for_approval` has no callers
   (no user-side Submit, no user-side status view).
3. **Branding enforcement** — `branding(setting_key, setting_value, enforced)` +
   an "Enforce" switch (`features.py:264-297`); `get_enforced_branding` has zero callers:
   nothing is ever forced. `max_videos_per_day` is never checked.
4. **Shared asset library** — `data/shared/{music,transitions,templates,branding}` listed and
   deleted by an admin panel; "add" = type a server-side path (`library_panel.py:85-101`);
   no browser upload; the DB `shared_templates` table supports delete only.
5. **Scheduled generation** — table + `_execute_scheduled_job` (`features.py:359-475`)
   spawning raw threads (bypassing the job queue); `start_scheduler()` runs inside the
   admin PAGE builder (`admin_dashboard.py:166-167`) so it only exists while an admin has
   `/admin` open; `schedule_job` has no callers. Zero jobs ever ran.
6. **Notifications** — full table + bell (`features.py:37-129`, 10 s poll); the only three
   producers sit in unreachable paths. `link` stored, never rendered.
7. **Audit log** — there is no audit table: the panel reads the `metrics` table
   (`audit_panel.py:20-24`), which has five call sites (login/logout/password change +
   scheduled runs). No admin action is recorded. No filter control. Retention = a manual
   "Purge" button; CSV "export" writes a server temp file and toasts its path.
8. **Admin dashboard** — `admin_dashboard.py`: six stat cards + eight rows (charts over
   metrics, sessions with rglob disk usage per refresh, users incl. business unit,
   scheduled + "recent generations" (metrics rows), library + templates, psutil/nvidia-smi
   system stats + DB backup/export/purge, maintenance incl. Shut Down Server, audit).
9. **Job queue** — `gui/enterprise/job_queue.py` is complete (per-type limits: COM export 1,
   TTS 4, encode 2, translation 3, QA 3; cancel; positions; stats) and `submit()` has NO
   callers. FEATURES.md admits "no generation lock; keep to one generation at a time".
10. **Business units** — a fixed dropdown on users, display/CSV only.

## Media Studio today (the gaps that matter)

- Projects are ownerless: `services/projects.py:100-116` writes no user; `list_projects()`
  returns everything; every route is `Depends(current_user)` with no filter — any editor
  can delete any project. Rendered output already lives in the project directory, so no
  directory restructure is needed: add `owner_id`/`owner_name`, filter lists (admins see
  all), enforce ownership on get/delete/generate/revoice/video, backfill legacy records
  as admin-owned (never "public").
- `services/jobs.py`: 2 workers, in-memory, no cancel, no per-kind limits. PowerPoint COM
  (`core/pptx_exporter.py:61-93`, GetActiveObject/Dispatch) is unguarded → **two concurrent
  deck generations race on one PowerPoint instance TODAY** (live bug).
- Studio settings (`services/studio_settings.py`) are already the "admin sets defaults,
  everyone reads" layer — what is missing is the LOCK.

## OpenSight shapes to port (`C:\Projects\OpenSight`)

- Audit: `core/activities.py:63-79` `audit(user_id, action, entity, entity_id, detail)` →
  `audit_log` (`db/models.py:926-936`, index on created_at DESC); `GET /api/admin/audit`
  (limit ≤ 1000, joined to users); called from every mutating admin endpoint.
- Notifications: `core/notifications.py:12-57` `notify`, `notify_roles`, `unread_count`,
  `mark_read`, `mark_all_read`; router = `GET /api/notifications` → {unread, items},
  `POST /read-all`, `POST /{id}/read`; polled by React Query.
- Admin page: `frontend/src/pages/Admin.tsx` (tabbed; non-admin EmptyState guard).
- Scheduler (only if scheduling ever ships): `core/scheduler.py` — a lifespan task with a
  DB lease, exposed at `api/routers/admin.py:136-151`.

## Recommended vertical 4, in build order

- **4a Ownership + audit** (correctness first): `owner_id` on the project record, list
  filtering, per-route checks, legacy backfill; `audit_log` table (users(id) nullable,
  denormalised username, action, entity, entity_id, detail, created_at) + `audit()` called
  from EVERY mutating endpoint (login/logout, project import/delete, generate, revoice,
  user create/patch/reset, settings update, asset upload); `GET /api/admin/audit`
  (limit, action, user filters) + `POST /api/admin/maintenance/purge-audit?days=`.
- **4b Notifications**: OpenSight's table (type CHECK: job|system|account|asset) + three
  endpoints + a bell in `layout/Shell.tsx`'s top bar; producers: job done/failed, account
  created/reset, settings changed (to admins).
- **4c Branding lock**: `locked_fields: list[str]` in studio settings (validated against
  FIELDS); on generate, after schema validation, overwrite locked fields SERVER-SIDE with
  the studio value before constructing VideoProcessor; `GET /api/settings/studio` returns
  the locked set so the generate card renders them disabled ("set by your administrator").
  Test: a client sending a locked `watermark_text` gets the admin's value.
- **4d Admin page** `/admin` mirroring OpenSight's tabs (Overview / Users / Audit / Jobs /
  Storage); move AccountsCard + PasswordPolicyCard there; `GET /api/admin/stats` (counts,
  disk from summed `size_bytes` — never an rglob per request; GPU/psutil optional and
  cached), `GET /api/admin/jobs`, `GET /api/admin/storage` (per owner), `GET /api/jobs`
  (own; admins all).
- **4e COM lock + per-kind job limits** (belongs with vertical 3 if that lands first):
  a module-level lock around the slide-export stage (job reports "waiting for
  PowerPoint" via progress), a kind-aware semaphore map in `services/jobs.py`
  ({generate: 1, transcribe: 2, revoice: 2}) so `status="queued"` is visible.
- **Defer/drop**: approval workflow (nothing reusable, never used), scheduled generation
  (needs a persistent queue + lifespan scheduler — its own vertical), shared asset library
  (= vertical 2b), business units (add the column only when something filters on it),
  self-registration (do not port), per-user config (`SessionConfig` — never wired).

## Data model notes

Ownership stays in `project.json` (keeps "a project is a directory you can zip"; avoids
DB rows rotting against deleted directories). Audit + notifications in `media_studio.db`
via the existing `init_db()` migration idiom. Branding lock in `data/config.json` via
studio settings (no new table).

## Traps

- A `locked` flag with no server-side enforcement is worse than none (SlideStudio's
  `get_enforced_branding`, zero callers). Ship the override and its test together.
- Missing `owner_id` on legacy records = admin-owned, logged once; never "visible to all".
- The actor always comes from `Depends(current_user)` — never a literal "admin".
- Disk usage: sum stored `size_bytes`, never rglob per refresh.
- A scheduler inside a page builder is not a scheduler; a lifespan task with a lease is.
- Audit growth: cap reads, index created_at DESC, ship the purge endpoint with the table.
- CSV export must stream to the browser (`StreamingResponse`), never a server temp path.
- Job persistence: the write must land in the same change as the read (SlideStudio's
  `job_history` table never got a row).
