"""YouTube upload via YouTube Data API v3.

Requires:
- pip install google-auth google-auth-oauthlib google-api-python-client
- A YouTube Data API v3 OAuth client ID (client_secrets.json)

The first upload will open a browser for OAuth consent. Credentials are cached
in data/youtube_credentials.json for subsequent uploads.
"""

from pathlib import Path
from typing import Optional, Dict
import logging

logger = logging.getLogger("mediastudio.YOUTUBE")

# Scopes needed for upload
YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_API_SERVICE = "youtube"
YOUTUBE_API_VERSION = "v3"


def check_youtube_dependencies() -> bool:
    """Check if YouTube API dependencies are installed."""
    try:
        import google.auth  # noqa: F401 - availability probe
        import google_auth_oauthlib  # noqa: F401 - availability probe
        import googleapiclient  # noqa: F401 - availability probe
        return True
    except ImportError:
        return False


def get_credentials_path(data_dir: Path) -> Path:
    """Path to cached OAuth credentials."""
    return data_dir / "youtube_credentials.json"


def get_client_secrets_path(data_dir: Path) -> Path:
    """Path to OAuth client secrets file."""
    return data_dir / "client_secrets.json"


def is_configured(data_dir: Path) -> bool:
    """Check if YouTube upload is configured (client_secrets.json exists)."""
    return get_client_secrets_path(data_dir).exists()


def authenticate(data_dir: Path) -> Optional[object]:
    """Authenticate with YouTube API. Opens browser on first use.

    Returns an authorized YouTube API service object, or None on failure.
    """
    if not check_youtube_dependencies():
        logger.error("YouTube dependencies not installed. Run: pip install google-auth google-auth-oauthlib google-api-python-client")
        return None

    secrets_path = get_client_secrets_path(data_dir)
    if not secrets_path.exists():
        logger.error("client_secrets.json not found in %s", data_dir)
        return None

    creds_path = get_credentials_path(data_dir)
    credentials = None

    # Load cached credentials
    if creds_path.exists():
        try:
            from google.oauth2.credentials import Credentials
            credentials = Credentials.from_authorized_user_file(str(creds_path), [YOUTUBE_UPLOAD_SCOPE])
        except Exception:
            credentials = None

    # Refresh or get new credentials
    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            try:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
            except Exception:
                credentials = None

        if not credentials:
            try:
                from google_auth_oauthlib.flow import InstalledAppFlow
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(secrets_path), [YOUTUBE_UPLOAD_SCOPE]
                )
                credentials = flow.run_local_server(port=0)
            except Exception as e:
                logger.error("OAuth flow failed: %s", e)
                return None

        # Cache credentials
        if credentials:
            creds_path.write_text(credentials.to_json(), encoding="utf-8")

    try:
        from googleapiclient.discovery import build
        return build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, credentials=credentials)
    except Exception as e:
        logger.error("Failed to build YouTube service: %s", e)
        return None


def upload_video(
    data_dir: Path,
    video_path: Path,
    title: str,
    description: str = "",
    tags: Optional[list] = None,
    category_id: str = "22",  # People & Blogs
    privacy: str = "private",
    on_progress=None,
) -> Optional[Dict]:
    """Upload a video to YouTube.

    Args:
        data_dir: directory containing client_secrets.json
        video_path: path to the MP4 file
        title: video title (max 100 chars)
        description: video description (max 5000 chars)
        tags: list of tags
        category_id: YouTube category ID (22=People & Blogs, 27=Education, 28=Science & Tech)
        privacy: "private", "unlisted", or "public"
        on_progress: callback(fraction, message)

    Returns dict with 'id', 'url' on success, None on failure.
    """
    if not video_path.exists():
        logger.error("Video file not found: %s", video_path)
        return None

    youtube = authenticate(data_dir)
    if not youtube:
        return None

    if on_progress:
        on_progress(0.1, "Authenticated, preparing upload...")

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(
            str(video_path),
            mimetype="video/mp4",
            resumable=True,
            chunksize=10 * 1024 * 1024,  # 10MB chunks
        )

        request = youtube.videos().insert(
            part=",".join(body.keys()),
            body=body,
            media_body=media,
        )

        if on_progress:
            on_progress(0.2, "Uploading...")

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and on_progress:
                on_progress(0.2 + status.progress() * 0.7, f"Uploading... {int(status.progress() * 100)}%")

        video_id = response.get("id", "")
        result = {
            "id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
        }

        if on_progress:
            on_progress(1.0, f"Upload complete! {result['url']}")

        logger.info("Video uploaded: %s", result['url'])
        return result

    except Exception as e:
        logger.error("Upload failed: %s", e)
        if on_progress:
            on_progress(0, f"Upload failed: {e}")
        return None
