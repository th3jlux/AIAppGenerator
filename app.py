import json
import logging
import os

from flask import Flask, render_template, request, jsonify, send_from_directory
from importlib import import_module

from config import Config
from helper import (
    install_package,
    save_route_code,
    get_existing_code,
    sanitize_route_name,
    trigger_reload,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_functions():
    """Read the registered utilities from functions.json (empty list on error)."""
    try:
        with open(Config.FUNCTIONS_JSON, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        logger.error("functions.json is not valid JSON.")
        return []


def register_blueprints_from_json(app):
    """Import and register every blueprint listed in functions.json.

    Returns a list of human-readable error strings for any that failed to load,
    so the home page can surface them instead of crashing the whole app.
    """
    errors = []
    for function in load_functions():
        blueprint_name = function.get("bluePrint", "<unknown>")
        try:
            python_file = function["python_file"].replace(".py", "")
            module = import_module(f"routes.{python_file}")
            blueprint = getattr(module, blueprint_name)
            app.register_blueprint(blueprint)
            logger.info("Registered blueprint '%s'.", blueprint_name)
        except Exception as e:  # noqa: BLE001 - report, don't crash startup
            msg = f"Failed to load blueprint '{blueprint_name}': {e}"
            logger.error(msg)
            errors.append(msg)
    return errors


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.from_object(Config)
    app.secret_key = Config.SECRET_KEY

    blueprint_errors = register_blueprints_from_json(app)

    @app.route("/")
    def home():
        links = [{"href": f["href"], "title": f["href"]} for f in load_functions()]
        return render_template("home.html", links=links, errors=blueprint_errors)

    @app.route("/routes/<path:filename>")
    def serve_routes(filename):
        return send_from_directory("routes", filename)

    @app.route("/submit", methods=["POST"])
    def submit():
        data = request.get_json(silent=True) or {}
        title = data.get("title")
        prompt = data.get("prompt")
        is_update = bool(data.get("selectedUtilityValue"))

        if not title or not prompt:
            return jsonify({"error": "Title and prompt are required."}), 400

        try:
            route_name = sanitize_route_name(title)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        if is_update:
            user_prompt = (
                f"Update the utility with route_name: {route_name} whose current code "
                f"is {get_existing_code(route_name)} based on this prompt: {prompt}"
            )
        else:
            existing = {sanitize_route_name(f["href"]) for f in load_functions()}
            if route_name in existing:
                return jsonify({"error": "A utility with that title already exists."}), 400
            user_prompt = f"Create a utility with route_name: {route_name}, prompt: {prompt}"

        try:
            href = save_route_code(route_name, user_prompt)
        except Exception as e:  # noqa: BLE001
            logger.error("Generation failed: %s", e)
            return jsonify({"error": f"Generation failed: {e}"}), 500

        trigger_reload()
        return jsonify({"message": "Utility ready.", "href": href}), 200

    @app.route("/chatbot", methods=["POST"])
    def chatbot():
        data = request.get_json(silent=True) or {}
        title = data.get("title")
        prompt = data.get("prompt")

        if not title or not prompt:
            return jsonify({"error": "Title and prompt are required."}), 400

        try:
            route_name = sanitize_route_name(title)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        try:
            user_prompt = (
                f"Update the utility with route_name: {route_name} whose current code "
                f"is {get_existing_code(route_name)} based on this prompt: {prompt}"
            )
            save_route_code(route_name, user_prompt)
        except Exception as e:  # noqa: BLE001
            logger.error("Chatbot update failed: %s", e)
            return jsonify({"error": str(e)}), 500

        trigger_reload()
        return jsonify({"message": "Page updated."}), 200

    @app.route("/get_code", methods=["GET"])
    def get_code():
        route_name = request.args.get("route_name")
        if not route_name:
            return jsonify({"error": "Route name is required."}), 400
        try:
            route_name = sanitize_route_name(route_name)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

        result = get_existing_code(route_name)
        if not result["python_code"] and not result["html_code"]:
            return jsonify({"error": "No code found for the specified route."}), 404
        return jsonify(result)

    @app.route("/install", methods=["POST"])
    def install():
        data = request.get_json(silent=True) or {}
        package = data.get("package")
        if not package:
            return jsonify({"error": "Please provide a package name."}), 400
        return jsonify({"message": install_package(package)})

    @app.route("/delete", methods=["POST"])
    def delete_tool():
        data = request.get_json(silent=True) or {}
        title = data.get("title")
        if not title:
            return jsonify({"message": "No title provided."}), 400

        try:
            route_name = sanitize_route_name(title)
        except ValueError as e:
            return jsonify({"message": str(e)}), 400

        href = f"/{route_name}_html"
        functions = load_functions()
        updated = [f for f in functions if f["href"] != href]
        if len(updated) == len(functions):
            return jsonify({"message": "Entry not found."}), 404

        with open(Config.FUNCTIONS_JSON, "w") as f:
            json.dump(updated, f, indent=4)

        for path in (
            os.path.join(Config.ROUTES_DIR, f"{route_name}_python.py"),
            os.path.join(Config.TEMPLATES_DIR, f"{route_name}_html.html"),
        ):
            if os.path.exists(path):
                os.remove(path)
                logger.info("Deleted file: %s", path)

        trigger_reload()
        return jsonify({"message": "Tool deleted successfully."}), 200

    @app.errorhandler(404)
    def page_not_found(e):
        return render_template("errors.html", message="Page not found."), 404

    @app.errorhandler(Exception)
    def handle_exception(e):
        logger.error("Unhandled exception: %s", e)
        # Only reveal the raw error in debug; otherwise show a generic message.
        message = str(e) if app.debug else "An internal error occurred."
        return render_template("errors.html", message=message), 500

    return app


app = create_app()


if __name__ == "__main__":
    # The reload-trigger file is watched so generating/deleting a utility reloads the
    # server without ever modifying source files.
    open(Config.RELOAD_TRIGGER, "a").close()
    app.run(
        debug=Config.DEBUG,
        host=Config.HOST,
        port=Config.PORT,
        extra_files=[Config.RELOAD_TRIGGER],
    )
