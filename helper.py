import json
import os
import sys
import subprocess
from openai import OpenAI

base_path = os.path.dirname(__file__)  # Base directory of the installed package
routes_dir = os.path.join(base_path, 'routes')
templates_dir = os.path.join(base_path, 'templates')
key_path = os.path.join(base_path, "key.txt")

with open(key_path, "r") as key_file:
    api_key = key_file.read().strip()

client = OpenAI(api_key=api_key)

def ensure_directories():
    """Ensure required directories exist inside the package."""
    os.makedirs(routes_dir, exist_ok=True)
    os.makedirs(templates_dir, exist_ok=True)

def install_package(package):
    """Install a Python package using pip."""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        return f"Package '{package}' installed successfully."
    except subprocess.CalledProcessError as e:
        return f"Error installing package '{package}': {e}"

def get_existing_code(route_name):
    python_file_path = os.path.join(routes_dir, f"{route_name}_python.py")
    html_file_path = os.path.join(templates_dir, f"{route_name}_html.html")

    python_code = ""
    html_code = ""

    # Read the Python file if it exists
    if os.path.exists(python_file_path):
        with open(python_file_path, 'r') as file:
            python_code = file.read()

    # Read the HTML file if it exists
    if os.path.exists(html_file_path):
        with open(html_file_path, 'r') as file:
            html_code = file.read()

        # Remove unwanted strings from the HTML code
        strings_to_remove = [
            '{% endblock %}',
            '{% extends "base.html" %}',
            '{% block content %}'
        ]
        for target_string in strings_to_remove:
            html_code = html_code.replace(target_string, "")

    result = {
        "python_code": python_code.strip(),
        "html_code": html_code.strip()
    }

    return result

def write_to_file(file_path, content):
    """Write a string to a file, handling escaped characters properly."""
    try:
        # Unescape special characters
        content = content.encode('utf-8').decode('unicode_escape')
        with open(file_path, 'w') as file:
            file.write(content)
        print(f"Content successfully written to {file_path}")
    except Exception as e:
        print(f"An error occurred while writing to the file: {e}")

def update_functions_json(route_name):
    """Update the functions.json file with a new route entry, only if it doesn't already exist."""
    new_entry = {
        "bluePrint": f"{route_name}_blueprint",
        "href": f"/{route_name}_html",
        "template": f"{route_name}_html.html",
        "python_file": f"{route_name}_python.py"
    }

    try:
        json_path = os.path.join(base_path, "functions.json")
        if os.path.exists(json_path):
            with open(json_path, 'r') as functions_file:
                functions = json.load(functions_file)
        else:
            functions = []

        # Check if the entry already exists
        if any(function.get('href') == new_entry['href'] for function in functions):
            print(f"Entry for {new_entry['href']} already exists. No update made.")
            return

        functions.append(new_entry)

        with open(json_path, 'w') as functions_file:
            json.dump(functions, functions_file, indent=4)

        print(f"functions.json updated with new route: {new_entry}")
    except Exception as e:
        print(f"An error occurred while updating functions.json: {e}")

def generate_openai_response(prompt):
    """Generate Python and HTML code using OpenAI API."""
    print("openai entered")
    system_prompt = """You are an assistant that generates Flask utilities. 

- The "python_code" field should include the Python code for the Flask routes and logic (excluding the `if __name__ == '__main__'` block). 
  The rest of the code should be complete and executable without placeholders. Add extensive error handling and display the errors to users.
  The user has the latest versions of python and python packages.
  The route decorator should be of the format @<route_name>_blueprint.route('/<route_name>_html', methods=['GET', 'POST'])
  There should also be a line for blueprint declaration above this method in the form <route_name>_blueprint = Blueprint('<route_name>_blueprint', __name__)
  This file will be saved locally as <route_name>_python.py
  You should also return the packages used by the code you generated separated by newline in pip_installs field.
- The "html_code" field should include the full code for the HTML template for the corresponding utility page.
  The html_code should include CSS to make sure the page looks gorgeous and modern.
  This file will be saved locally as <route_name>_html.html
"""

    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt}
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
                            "pip_installs": {"type": "string"}
                        }
                    }
                }
            }
        )

        response_content = response.choices[0].message.content.strip()
        print("response_content", response_content)

        # Handle code fences
        if response_content.startswith("```json") and response_content.endswith("```"):
            response_content = response_content[7:-3].strip()
        elif response_content.startswith("```") and response_content.endswith("```"):
            response_content = response_content[4:-3].strip()

        return json.loads(response_content)

    except Exception as e:
        raise RuntimeError(f"Error while communicating with OpenAI API: {e}")

def save_route_code(route_name, prompt):
    """Generate and save Python and HTML code for a route."""
    try:
        generated_content = generate_openai_response(prompt)

        if "python_code" not in generated_content or "html_code" not in generated_content:
            raise ValueError("Response must contain 'python_code' and 'html_code' keys.")

        python_code = generated_content["python_code"]
        html_code = generated_content["html_code"]

        ensure_directories()

        python_file_path = os.path.join(routes_dir, f"{route_name}_python.py")
        html_file_path = os.path.join(templates_dir, f"{route_name}_html.html")

        write_to_file(python_file_path, python_code)
        print("Write Success to", python_file_path)

        html_code = f"""
{{% extends "base.html" %}}
{{% block content %}}
{html_code.strip()}
{{% endblock %}}
"""
        write_to_file(html_file_path, html_code)
        print("Write Success to", html_file_path)

        update_functions_json(route_name)

    except Exception as e:
        print(f"An unexpected error occurred: {e}")
