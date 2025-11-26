from flask import Flask, render_template, request, jsonify, Blueprint
import json
import os

# Blueprint declaration
deutsch_recap_blueprint = Blueprint('deutsch_recap_blueprint', __name__)

data_file_path = os.path.join('data', 'progress.json')

@deutsch_recap_blueprint.route('/deutsch_recap_html', methods=['GET', 'POST'])
def deutsch_recap_html():
    try:
        # Load the data from the JSON file
        with open(data_file_path, 'r', encoding='utf-8') as file:
            data = json.load(file)
    except FileNotFoundError:
        return "Error: Data file not found. Please make sure 'progress.json' exists in the 'data' folder.", 500
    except json.JSONDecodeError as e:
        return f"Error: Data file is corrupted. {str(e)}", 500

    filtered_words = []

    if request.method == 'POST':
        if 'remove_word' in request.form:
            level = request.form.get('level_to_remove')
            word_deutsch = request.form.get('word_deutsch')
            try:
                for word in data[level]:
                    if word.get('deutsch') == word_deutsch:
                        word['difficulty'] = 'normal'  # Change difficulty to normal
                        break
                with open(data_file_path, 'w', encoding='utf-8') as file:
                    json.dump(data, file, indent=4)
            except KeyError as e:
                return f"Error: {str(e)} not found in data.", 500
            except Exception as e:
                return f"An unexpected error occurred: {str(e)}", 500
        else:
            # Normal filtering
            level_filter = request.form.get('level')
            status_filter = request.form.get('status')
            difficulty_filter = request.form.get('difficulty')
            min_incorrect_count = request.form.get('min_incorrect_count', type=int, default=0)
            max_incorrect_count = request.form.get('max_incorrect_count', type=int)

            # Filtering
            for level, words in data.items():
                if level_filter and level != level_filter:
                    continue

                for word in words:
                    if status_filter and word.get('status') != status_filter:
                        continue

                    if difficulty_filter == 'hard' and word.get('difficulty') != 'hard':
                        continue

                    # Range filtering for incorrect count
                    word_incorrect_count = word.get('incorrect_count', 0)
                    
                    # Check minimum threshold
                    if word_incorrect_count < min_incorrect_count:
                        continue
                    
                    # Check maximum threshold (if provided)
                    if max_incorrect_count is not None and word_incorrect_count > max_incorrect_count:
                        continue

                    filtered_words.append(word)
    else:
        # GET request - show all words by default
        for level, words in data.items():
            for word in words:
                filtered_words.append(word)

    return render_template('deutsch_recap_html.html', words=filtered_words, levels=data.keys())