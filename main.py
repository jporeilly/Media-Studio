"""Media Studio Enterprise - server entry point.

Usage:
  python main.py                # serve API + built frontend on the configured port (default 5680)
  python main.py --port 7000    # override the port
  python main.py --no-browser   # do not open a browser tab

Mirrors OpenSight's ``main.py``: it puts the repo root on ``sys.path``, kills any
previous instance holding the port, and runs uvicorn. The default port (5680)
sits outside the 5580-5659 range the desktop SlideStudio edition uses.
"""

import argparse
import os
import subprocess
import sys
import threading
import webbrowser

DEFAULT_PORT = 5680


def kill_previous_instance(port: int):
    """Kill any process currently listening on the configured port."""
    if os.name == "nt":
        try:
            result = subprocess.run(f'netstat -aon | findstr :{port} | findstr LISTENING',
                                    capture_output=True, text=True, shell=True)
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[-1] != str(os.getpid()):
                    subprocess.run(f"taskkill /F /PID {parts[-1]}", capture_output=True, shell=True)
        except Exception:
            pass
    else:
        try:
            subprocess.run(f"lsof -ti:{port} | xargs -r kill -9", capture_output=True, shell=True)
        except Exception:
            pass


def main():
    this_dir = os.path.dirname(os.path.abspath(__file__))
    if this_dir not in sys.path:
        sys.path.insert(0, this_dir)
    os.chdir(this_dir)

    parser = argparse.ArgumentParser(description="Media Studio Enterprise server")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--keep-running", action="store_true", help="do not kill an existing instance on the port")
    args = parser.parse_args()

    from api import __version__
    from utils.config import config
    port = args.port or config._config.get("port") or DEFAULT_PORT

    if not args.keep_running and not os.environ.get("MEDIA_STUDIO_KEEP_RUNNING"):
        kill_previous_instance(port)

    try:
        import uvicorn
        from api.app import app, FRONTEND_DIST
    except ImportError as exc:
        print(f"[ERROR] Missing dependency: {exc}")
        print("Install requirements: pip install -r requirements.txt")
        sys.exit(1)

    url = f"http://{'localhost' if args.host in ('0.0.0.0', '127.0.0.1') else args.host}:{port}"
    print(f"  Media Studio Enterprise {__version__}")
    print(f"  Serving on {url}   (API explorer: {url}/api/swagger)")
    if not FRONTEND_DIST.exists():
        print("  Frontend not built yet: cd frontend && npm install && npm run build")

    open_browser = not args.no_browser and not os.environ.get("MEDIA_STUDIO_NO_BROWSER")
    if open_browser and FRONTEND_DIST.exists():
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run(app, host=args.host, port=port, log_level="info")
    except KeyboardInterrupt:
        print("\nShutting down Media Studio Enterprise.")


if __name__ == "__main__":
    main()
