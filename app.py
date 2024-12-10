from helper import generate_openai_response, save_route_code, get_existing_code
import json
import logging
from flask import Flask, render_template, send_from_directory
from flask import request, jsonify
from importlib import import_module
import os

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.urandom(24)  

def create_app():
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')
    app.secret_key = os.urandom(24)  
    
    @app.route("/")
    def home():
        return render_template("home.html")

    return app


@app.route('/install', methods=['POST'])
def install():
    """API endpoint to install a Python package."""
    data = request.json
    if not data or 'package' not in data:
        return jsonify({'error': 'Please provide a package name.'}), 400

    package_name = data['package']
    result = install_package(package_name)
    return jsonify({'message': result})

@app.route('/delete', methods=['POST'])
def delete_tool():
    data = request.json
    title = data.get('title')

    if not title:
        return jsonify({"message": "No title provided"}), 400

    try:
        # Remove entry from functions.json
        json_path = os.path.join(os.path.dirname(__file__), "functions.json")
        with open(json_path, 'r') as f:
            functions = json.load(f)

        updated_functions = [f for f in functions if f["href"] != title]

        # Check if the entry was removed
        if len(updated_functions) == len(functions):
            return jsonify({"message": "Entry not found"}), 404

        json_path = os.path.join(os.path.dirname(__file__), "functions.json")
        with open(json_path, 'w') as f:
            json.dump(updated_functions, f, indent=4)

        # Delete associated Python and HTML files
        route_name = title.strip('/')


        route_name = route_name[:-5]
        python_file_path = os.path.join('routes', f"{route_name}_python.py")
        html_file_path = os.path.join('templates', f"{route_name}_html.html")

        for file_path in [python_file_path, html_file_path]:
            print(file_path)
            if os.path.exists(file_path):
                os.remove(file_path)
                logger.info(f"Deleted file: {file_path}")
        restart()
        return jsonify({"message": "Tool deleted successfully"}), 200
    except Exception as e:
        logger.error(f"Error deleting tool '{title}': {str(e)}")
        return jsonify({"message": f"An error occurred: {str(e)}"}), 500

# Serve files from the routes folder
@app.route('/routes/<path:filename>')
def serve_routes(filename):
    return send_from_directory('routes', filename)

def register_blueprints_from_json(app, json_file='functions.json'):
    errors = []  # Track errors for user feedback

    try:
        json_path = os.path.join(os.path.dirname(__file__), "functions.json")
        with open(json_path, 'r') as f:
            functions = json.load(f)
    except FileNotFoundError:
        error_msg = f"JSON file '{json_file}' not found."
        logger.error(error_msg)
        errors.append(error_msg)
        return errors
    except json.JSONDecodeError:
        error_msg = f"Error decoding JSON file '{json_file}'."
        logger.error(error_msg)
        errors.append(error_msg)
        return errors

    for function in functions:
        try:
            # Extract blueprint details
            python_file = function["python_file"].replace(".py", "")  # Remove .py extension
            blueprint_name = function["bluePrint"]

            # Dynamically import the module and get the blueprint
            module = import_module(f"routes.{python_file}")
            blueprint = getattr(module, blueprint_name)

            # Register the blueprint
            app.register_blueprint(blueprint)
            logger.info(f"Successfully registered blueprint '{blueprint_name}' from '{python_file}.py'.")
        except (ImportError, AttributeError) as e:
            error_msg = f"Failed to load blueprint '{blueprint_name}' from '{python_file}.py': {e}"
            logger.error(error_msg)
            errors.append(error_msg)
        except Exception as e:
            error_msg = f"Unexpected error while loading blueprint '{blueprint_name}': {e}"
            logger.error(error_msg)
            errors.append(error_msg)

    return errors

# Register all blueprints
blueprint_errors = register_blueprints_from_json(app)

@app.route('/')
def home():
    # Load links dynamically from functions.json
    try:
        json_path = os.path.join(os.path.dirname(__file__), "functions.json")
        with open(json_path, 'r') as f:
            functions = json.load(f)
            links = [{"href": f["href"], "title": f["href"]} for f in functions]
    except Exception as e:
        logger.error(f"Error loading links: {e}")
        links = []

    return render_template('home.html', links=links, errors=blueprint_errors)

@app.route('/restart', methods=['POST'])
def restart():
    """Toggle a space in the home.html file and restart the application."""
    template_path = os.path.join(app.root_path, 'app.py')
    
    # Read the current content of home.html
    with open(template_path, 'r') as file:
        content = file.read()
    
    # Check if there's a space at the end of the file
    if content.endswith(' '):
        # Remove the trailing space
        content = content.rstrip('<a></a>')
        print("removed the space")
    else:
        # Add a space to the end of the file
        content += ' '
        print("added the space")
    
    # Write the updated content back to home.html
    with open(template_path, 'w') as file:
        file.write(content)
        print("write complete")
    return "all good", 200

@app.route('/chatbot', methods=['POST'])
def chatbot():
    data = request.json
    title = data.get('title')
    prompt = data.get('prompt')

    if not title or not prompt:
        return jsonify({"error": "Title and prompt are required."}), 400

    try:
        # Construct the user prompt
        user_prompt = f"""Update the utility with route_name: {title} whose current code 
        is {get_existing_code(title)} based on this prompt: {prompt}"""

        # Call save_route_code
        save_route_code(title, user_prompt)

        restart()

        return jsonify({"message": "Chatbot successfully updated."}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/get_code', methods=['GET'])
def get_code():
    # Get the 'route_name' parameter from the query string
    route_name = request.args.get('route_name')

    if not route_name:
        return jsonify({"error": "Route name is required"}), 400

    # Use the get_existing_code function to retrieve the code
    result = get_existing_code(route_name)

    # Check if both files are empty
    if not result["python_code"] and not result["html_code"]:
        return jsonify({"error": "No code found for the specified route"}), 404

    return jsonify(result)

@app.errorhandler(Exception)
def handle_exception(e):
    # Log the error for debugging
    print(f"Unhandled exception: {e}")
    return render_template('errors.html', message=f"An error occurred: {e}"), 500

@app.errorhandler(404)
def page_not_found(e):
    return render_template('errors.html', message=e), 404

@app.errorhandler(500)
def internal_server_error(e):
    return render_template('errors.html', message=e), 500

@app.errorhandler(Exception)
def handle_exception(e):
    # Log the full stack trace for debugging
    logger.error(f"Unhandled exception: {e}")
    return render_template('errors.html', message=e), 500

@app.route('/submit', methods=['POST'])
def submit():
    print("Entered Submit")
    data = request.json
    title = data.get('title')
    prompt = data.get('prompt')
    selectedUtilityValue = data.get('selectedUtilityValue')

    if(selectedUtilityValue):
        user_prompt = f"""Update the utility with route_name: {title} whose current code 
        is {get_existing_code(title)} based on this prompt: {prompt}"""
    else:
        json_path = os.path.join(os.path.dirname(__file__), "functions.json")
        with open(json_path, 'r') as f:
            functions = json.load(f)
            existing_titles = [f["href"] for f in functions]

        if title in existing_titles:
            return jsonify({"error": "Title already exists"}), 400

        # the helper function saves the response into a temp.json file
        user_prompt = f"Create a utility with route_name: {title}, prompt: {prompt}"

    save_route_code(title, user_prompt)
    
    restart()
    return "all good", 200

if __name__ == '__main__':
    app.run(debug=True, port=5001) 