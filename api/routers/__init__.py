"""API routers, one per area, and the registry ``api.app.create_app`` mounts them from.

``API_ROUTERS`` are mounted under ``/api`` in this order.
"""

from api.routers import (
    admin, ai, auth, capture, captures, docs, edit, health, jobs, media, music, narration, projects, recordings,
    settings, slides, updates, users,
)

API_ROUTERS = [
    health.router, auth.router, users.router, settings.router,
    projects.router, slides.router, narration.router, edit.router, music.router, ai.router, jobs.router,
    media.router, docs.router, updates.router, admin.router,
    # Screen capture (T3): stills and the overlay's frozen frames, the
    # Captures list, and the recorder's chunks.
    capture.router, captures.router, recordings.router,
]
