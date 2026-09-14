"""API routers, one per area, and the registry ``api.app.create_app`` mounts them from.

``API_ROUTERS`` are mounted under ``/api`` in this order.
"""

from api.routers import auth, health, jobs, media, projects, settings, updates, users

API_ROUTERS = [
    health.router, auth.router, users.router, settings.router,
    projects.router, jobs.router, media.router, updates.router,
]
