from flask import Flask, render_template, request, jsonify, Blueprint
import json
import os

# Blueprint declaration
deutsch_recap_blueprint = Blueprint('deutsch_recap_blueprint', __name__)

data_file_path = os.path.join('data', 'progress2.json')

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
    
    # Initialize filter variables
    current_level_filter = ''
    current_status_filter = ''
    current_difficulty_filter = ''
    current_min_incorrect = 0
    current_max_incorrect = None

    if request.method == 'POST':
        if 'bulk_remove' in request.form:
            # Bulk word removal
            selected_words = request.form.getlist('selected_words')
            print("selected_words" + str(selected_words))
            
            # Get the preserved filter state from hidden inputs
            current_level_filter = request.form.get('current_level_filter', '')
            current_status_filter = request.form.get('current_status_filter', '')
            current_difficulty_filter = request.form.get('current_difficulty_filter', '')
            current_min_incorrect = int(request.form.get('current_min_incorrect', 0))
            current_max_incorrect = request.form.get('current_max_incorrect')
            if current_max_incorrect == '':
                current_max_incorrect = None
            else:
                current_max_incorrect = int(current_max_incorrect) if current_max_incorrect else None
            
            try:
                # Process each selected word
                changes_made = 0
                for word_info in selected_words:
                    level, word_deutsch = word_info.split('|', 1)
                    for word in data[level]:
                        if word.get('deutsch') == word_deutsch:
                            # Always set difficulty and reset incorrect_count when removing from hard list
                            word['difficulty'] = 'normal'  # Change difficulty to normal
                            word['incorrect_count'] = 0    # Reset incorrect count to 0
                            changes_made += 1
                            break
                
                if changes_made > 0:
                    with open(data_file_path, 'w', encoding='utf-8') as file:
                        json.dump(data, file, indent=4, ensure_ascii=False)
            except Exception as e:
                return f"An unexpected error occurred: {str(e)}", 500
            
        else:
            # Normal filtering - get values from filter form
            current_level_filter = request.form.get('level', '')
            current_status_filter = request.form.get('status', '')
            current_difficulty_filter = request.form.get('difficulty', '')
            current_min_incorrect = request.form.get('min_incorrect_count', type=int, default=0)
            current_max_incorrect = request.form.get('max_incorrect_count', type=int)

        # Reapply filters for all POST operations
        for level, words in data.items():
            if current_level_filter and level != current_level_filter:
                continue

            for word in words:
                if current_status_filter and word.get('status') != current_status_filter:
                    continue

                # Difficulty filtering - treat missing difficulty as 'normal'
                word_difficulty = word.get('difficulty', 'normal')
                if current_difficulty_filter == 'hard' and word_difficulty != 'hard':
                    continue

                # Range filtering for incorrect count
                word_incorrect_count = word.get('incorrect_count', 0)
                
                # Check minimum threshold
                if word_incorrect_count < current_min_incorrect:
                    continue
                
                # Check maximum threshold (if provided)
                if current_max_incorrect is not None and word_incorrect_count > current_max_incorrect:
                    continue

                filtered_words.append(word)
    else:
        # GET request - show all words by default
        for level, words in data.items():
            for word in words:
                filtered_words.append(word)

    return render_template('deutsch_recap_html.html', 
                         words=filtered_words, 
                         levels=data.keys(),
                         current_level_filter=current_level_filter,
                         current_status_filter=current_status_filter,
                         current_difficulty_filter=current_difficulty_filter,
                         current_min_incorrect=current_min_incorrect,
                         current_max_incorrect=current_max_incorrect or '')