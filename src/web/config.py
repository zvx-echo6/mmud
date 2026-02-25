"""
Last Ember — Web dashboard configuration.
"""
import os

# Web dashboard
WEB_HOST = os.environ.get("MMUD_WEB_HOST", "0.0.0.0")
WEB_PORT = int(os.environ.get("MMUD_WEB_PORT", "5000"))
WEB_SECRET_KEY = os.environ.get("MMUD_WEB_SECRET", "change-me-in-production")
WEB_ADMIN_PASSWORD = os.environ.get("MMUD_ADMIN_PASSWORD", "admin")

# Polling intervals (seconds) — used by frontend JS
POLL_STATUS_INTERVAL = 30
POLL_BROADCAST_INTERVAL = 15

# DB path is shared with game engine — comes from root config or env
DB_PATH = os.environ.get("MMUD_DB_PATH", "mmud.db")
