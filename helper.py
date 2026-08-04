import json
import os
import re
import sys
import subprocess

from openai import OpenAI

try:  # OpenAI SDK v1 exception types (present in normal installs)
    from openai import (
        AuthenticationError,
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
        PermissionDeniedError,
        NotFoundError,
        APIStatusError,
    )
except Exception:  # pragma: no cover - defensive for unusual SDK versions
    class _MissingOpenAIError(Exception):
        pass
    AuthenticationError = RateLimitError = APIConnectionError = APITimeoutError = \
        PermissionDeniedError = NotFoundError = APIStatusError = _MissingOpenAIError

from config import Config

routes_dir = Config.ROUTES_DIR
templates_dir = Config.TEMPLATES_DIR

# Route names become file paths, so we only ever allow a safe identifier-like
# character set. This is the single chokepoint guarding against path traversal.
ROUTE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
# pip package specs: name plus optional version constraint / extras.
PACKAGE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]*(\[[A-Za-z0-9,._\-]+\])?([=<>!~]=?[A-Za-z0-9.\*]+)?$")


def _client():
    """Build the OpenAI client lazily so a missing key fails loudly at call time
    with an actionable message rather than at import time."""
    if not Config.OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Copy .env.example to .env and add your key "
            "(get one at https://platform.openai.com/api-keys)."
        )
    # Fail fast: short timeout and a single retry so a bad key/quota surfaces a
    # clear error in seconds instead of spamming retries and hanging the request.
    return OpenAI(api_key=Config.OPENAI_API_KEY, timeout=30, max_retries=1)


def sanitize_route_name(name):
    """Return a validated route name or raise ValueError.

    Accepts either a bare route name (``Word_Counter``) or an href/template form
    (``/Word_Counter_html``) and normalizes to the bare name.
    """
    if not name or not isinstance(name, str):
        raise ValueError("A name is required.")
    name = name.strip().lstrip("/")
    if name.endswith("_html"):
        name = name[: -len("_html")]
    if not ROUTE_NAME_RE.match(name):
        raise ValueError(
            "Invalid name. Use only letters, numbers and underscores (e.g. Word_Counter)."
        )
    return name


def validate_package_name(package):
    """Return a validated pip package spec or raise ValueError."""
    if not package or not isinstance(package, str):
        raise ValueError("A package name is required.")
    package = package.strip()
    if not PACKAGE_NAME_RE.match(package):
        raise ValueError(f"Invalid package name: {package!r}")
    return package


def ensure_directories():
    """Ensure required directories exist inside the package."""
    os.makedirs(routes_dir, exist_ok=True)
    os.makedirs(templates_dir, exist_ok=True)


def trigger_reload():
    """Touch the reload-trigger file so the Werkzeug reloader restarts the server.

    This replaces the old approach of mutating ``app.py`` in place (which could
    corrupt the file). The trigger file is registered via ``extra_files`` in
    ``app.run`` so touching it is enough to reload.
    """
    open(Config.RELOAD_TRIGGER, "a").close()
    os.utime(Config.RELOAD_TRIGGER, None)


def install_package(package):
    """Install a Python package using pip after validating its name."""
    try:
        package = validate_package_name(package)
    except ValueError as e:
        return str(e)
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        return f"Package '{package}' installed successfully."
    except subprocess.CalledProcessError as e:
        return f"Error installing package '{package}': {e}"


def get_existing_code(route_name):
    route_name = sanitize_route_name(route_name)
    python_file_path = os.path.join(routes_dir, f"{route_name}_python.py")
    html_file_path = os.path.join(templates_dir, f"{route_name}_html.html")

    python_code = ""
    html_code = ""

    if os.path.exists(python_file_path):
        with open(python_file_path, "r") as file:
            python_code = file.read()

    if os.path.exists(html_file_path):
        with open(html_file_path, "r") as file:
            html_code = file.read()

        # Strip the Jinja scaffolding so the user sees only their template body.
        for target_string in (
            '{% endblock %}',
            '{% extends "base.html" %}',
            '{% block content %}',
        ):
            html_code = html_code.replace(target_string, "")

    return {
        "python_code": python_code.strip(),
        "html_code": html_code.strip(),
    }


def write_to_file(file_path, content):
    """Write generated source to disk verbatim.

    The model already returns a normal (already-unescaped) string via JSON, so we
    write it directly. The previous ``unicode_escape`` round-trip corrupted any code
    containing backslashes or non-ASCII characters.
    """
    with open(file_path, "w") as file:
        file.write(content)


def update_functions_json(route_name):
    """Add a new route entry to functions.json if it isn't already present."""
    new_entry = {
        "bluePrint": f"{route_name}_blueprint",
        "href": f"/{route_name}_html",
        "template": f"{route_name}_html.html",
        "python_file": f"{route_name}_python.py",
    }

    json_path = Config.FUNCTIONS_JSON
    if os.path.exists(json_path):
        with open(json_path, "r") as functions_file:
            functions = json.load(functions_file)
    else:
        functions = []

    if any(fn.get("href") == new_entry["href"] for fn in functions):
        return

    functions.append(new_entry)
    with open(json_path, "w") as functions_file:
        json.dump(functions, functions_file, indent=4)


SYSTEM_PROMPT = """You are an assistant that generates self-contained Flask utilities.

- The "python_code" field contains ONLY the Flask blueprint and its logic (no
  `if __name__ == '__main__'` block, NO `app = Flask(__name__)`, and NO
  `app.register_blueprint(...)` call — the host application registers the blueprint).
  Declare the blueprint exactly as:
      <route_name>_blueprint = Blueprint('<route_name>_blueprint', __name__)
  and define the view with:
      @<route_name>_blueprint.route('/<route_name>_html', methods=['GET', 'POST'])
  Import everything you use. Add thorough error handling and surface errors to the user.
  Assume the latest versions of Python and all packages are available.
  This file is saved as <route_name>_python.py.
- The "html_code" field contains the full HTML template body for the page (it will be
  wrapped in `{% extends "base.html" %}` / `{% block content %}` automatically, so do
  not include those). Include scoped CSS so the page looks clean and modern.
  This file is saved as <route_name>_html.html.
- The "pip_installs" field lists any third-party packages used, one per line.
"""


def _friendly_openai_error(exc):
    """Translate an OpenAI SDK exception into an actionable, user-facing message."""
    model = Config.OPENAI_MODEL
    if isinstance(exc, AuthenticationError):
        return ("OpenAI rejected the API key (401). Check OPENAI_API_KEY in your .env "
                "is correct and active.")
    if isinstance(exc, RateLimitError):
        # 429 covers both hard rate-limiting and (much more commonly) an account
        # with no remaining credit / no billing set up.
        detail = getattr(exc, "message", "") or str(exc)
        if "insufficient_quota" in detail or "quota" in detail.lower():
            return ("OpenAI returned 'insufficient quota' (429): this key's account has no "
                    "remaining credits. Add a payment method or credits at "
                    "https://platform.openai.com/account/billing, then retry.")
        return ("OpenAI is rate-limiting these requests (429). Wait a moment and retry; if it "
                "persists, check your usage limits and billing at platform.openai.com.")
    if isinstance(exc, (PermissionDeniedError, NotFoundError)):
        return (f"The model '{model}' isn't available to this key/project. Enable it for the "
                f"project, or set OPENAI_MODEL in .env to one you can access (e.g. gpt-4o-mini).")
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return ("Could not reach OpenAI (network/timeout). Check your internet connection or "
                "any proxy/firewall, then retry.")
    if isinstance(exc, APIStatusError):
        status = getattr(exc, "status_code", "?")
        return f"OpenAI returned an error (HTTP {status}): {getattr(exc, 'message', str(exc))}"
    return f"Unexpected error calling OpenAI: {exc}"


def generate_openai_response(prompt):
    """Generate Python and HTML code using the OpenAI API."""
    try:
        response = _client().chat.completions.create(
            model=Config.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": "flask_builder",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "python_code": {"type": "string"},
                            "html_code": {"type": "string"},
                            "pip_installs": {"type": "string"},
                        },
                    },
                },
            },
        )
    except (AuthenticationError, RateLimitError, APIConnectionError, APITimeoutError,
            PermissionDeniedError, NotFoundError, APIStatusError) as exc:
        raise RuntimeError(_friendly_openai_error(exc)) from exc

    response_content = response.choices[0].message.content.strip()

    # Defensively strip code fences if the model wraps the JSON.
    if response_content.startswith("```json") and response_content.endswith("```"):
        response_content = response_content[7:-3].strip()
    elif response_content.startswith("```") and response_content.endswith("```"):
        response_content = response_content[3:-3].strip()

    return json.loads(response_content)


def save_route_code(route_name, prompt):
    """Generate and save the Python + HTML for a route. Returns the new href."""
    route_name = sanitize_route_name(route_name)
    generated_content = generate_openai_response(prompt)

    if "python_code" not in generated_content or "html_code" not in generated_content:
        raise ValueError("Response must contain 'python_code' and 'html_code' keys.")

    python_code = generated_content["python_code"]
    html_code = generated_content["html_code"]

    ensure_directories()

    python_file_path = os.path.join(routes_dir, f"{route_name}_python.py")
    html_file_path = os.path.join(templates_dir, f"{route_name}_html.html")

    write_to_file(python_file_path, python_code)

    wrapped_html = (
        '{% extends "base.html" %}\n'
        "{% block content %}\n"
        f"{html_code.strip()}\n"
        "{% endblock %}\n"
    )
    write_to_file(html_file_path, wrapped_html)

    update_functions_json(route_name)
    return f"/{route_name}_html"
