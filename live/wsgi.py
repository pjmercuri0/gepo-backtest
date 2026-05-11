"""Production WSGI entry point. Use with gunicorn:

    gunicorn -w 2 -b 0.0.0.0:8080 live.wsgi:app

Flask's built-in `app.run()` is for development only. In production, use
gunicorn (or any other WSGI server) which spawns multiple worker processes
and handles graceful shutdown.
"""
from live.webapp import app

# Make `app` importable so `gunicorn live.wsgi:app` works.
__all__ = ["app"]
