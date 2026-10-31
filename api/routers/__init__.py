"""API routers, one per area, and the registry ``api.app.create_app`` mounts them from.

``API_ROUTERS`` are mounted under ``/api`` in this order.
"""

from api.routers import (
    admin, ai, auth, capture, captures, courses, docs, edit, health, jobs, media, music, narration, projects, recordings,
    settings, slides, updates, users, video_to_course, vimeo, walkthroughs,
)

API_ROUTERS = [
    health.router, auth.router, users.router, settings.router,
    projects.router, slides.router, narration.router, edit.router, music.router, ai.router, jobs.router,
    media.router, docs.router, updates.router, admin.router,
    # Screen capture (T3): stills and the overlay's frozen frames, the
    # Captures list, and the recorder's chunks.
    capture.router, captures.router, recordings.router,
    # Walkthroughs (W1): steps made from stills, the builder's whole-document
    # save and the player's pictures. Courses (W2): the courses folder's
    # workshops and the preview of an import.
    walkthroughs.router, courses.router,
    # Vimeo (V2, V3): a rendered project video published to Vimeo, and its link placed in the course workshop's guide.
    vimeo.router,
    # Video to course (R1-R4): the one job that renders a walkthrough's video, publishes it and places the link.
    video_to_course.router,
]
