"""Application configuration.

All tunables live here so the rest of the app can stay declarative. Values are
read from the environment (with a local ``.env`` loaded by ``python-dotenv``) and
fall back to sensible local-dev defaults.
"""
import os

from dotenv import load_dotenv

# Load variables from a local .env file if present (no-op in production envs).
load_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


class Config:
    # --- Core Flask ---
    # A stable secret key keeps sessions valid across the dev reloader. If none is
    # provided we fall back to a random one (sessions reset on restart, which is fine
    # for a single-user local tool).
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY") or os.urandom(24)
    DEBUG = os.environ.get("FLASK_DEBUG", "1") not in ("0", "false", "False", "")

    # Bind to localhost only: this tool generates and executes code and can pip-install
    # packages, so it must never be exposed on a network interface by default.
    HOST = os.environ.get("FLASK_HOST", "127.0.0.1")
    PORT = int(os.environ.get("FLASK_PORT", "5001"))

    # --- Paths ---
    ROUTES_DIR = os.path.join(BASE_DIR, "routes")
    TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
    FUNCTIONS_JSON = os.path.join(BASE_DIR, "functions.json")
    # Touched to trigger the Werkzeug reloader without mutating source files.
    RELOAD_TRIGGER = os.path.join(BASE_DIR, ".reload_trigger")

    # --- OpenAI ---
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
