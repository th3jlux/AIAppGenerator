from flask import Flask, render_template, request, Blueprint, jsonify, session
import pandas as pd
import random
from datetime import datetime
import os
import json

Deutsch_Vocab_blueprint = Blueprint('Deutsch_Vocab_blueprint', __name__)

# Read vocabulary data
try:
    vocab_df = pd.read_csv('data/vocab.csv')
except Exception as e:
    vocab_df = pd.DataFrame(columns=['Artikel', 'Deutsch', 'English', 'Level'])
    print('Error loading vocabulary file:', e)

# Get distinct levels for filtering
level_options = vocab_df['Level'].dropna().unique()

# progress_data stores complete vocab state per level with status tracking
# Structure: { "A1.1": [{"artikel": "der", "deutsch": "Hund", "english": "dog", "status": "notyetanswered"}, ...], ... }
progress_data = {}

# Progress file on disk
PROGRESS_FILE = 'data/progress.json'

def load_progress():
    """Load progress from disk and ensure all vocab words are initialized with status"""
    global progress_data
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                progress_data = json.load(f)
        else:
            progress_data = {}
            
        # Initialize any missing levels with complete vocab
        initialize_missing_levels()
        
    except Exception as e:
        print('Error loading progress file:', e)
        progress_data = {}
        initialize_missing_levels()

def initialize_missing_levels():
    """Initialize progress for any levels not in progress_data and convert old format to new"""
    updated = False
    for level in level_options:
        if level not in progress_data:
            progress_data[level] = []
            # Add all words for this level with 'notyetanswered' status
            level_vocab = vocab_df[vocab_df['Level'] == level]
            for _, row in level_vocab.iterrows():
                artikel = row['Artikel'] if pd.notna(row['Artikel']) else ''
                deutsch = row['Deutsch']
                english = row['English']
                
                word_entry = {
                    'artikel': artikel,
                    'deutsch': deutsch,
                    'english': english,
                    'status': 'notyetanswered'
                }
                progress_data[level].append(word_entry)
            updated = True
        elif level in progress_data and len(progress_data[level]) > 0:
            # Check if this level uses old format (list of lists) and convert to new format
            first_item = progress_data[level][0]
            if isinstance(first_item, list):
                # Convert old format to new format
                old_format = progress_data[level]
                progress_data[level] = []
                
                for item in old_format:
                    if len(item) >= 3:  # [artikel, deutsch, english]
                        word_entry = {
                            'artikel': item[0] if item[0] else '',
                            'deutsch': item[1],
                            'english': item[2],
                            'status': 'notyetanswered'
                        }
                        progress_data[level].append(word_entry)
                updated = True
    
    if updated:
        save_progress()

def save_progress():
    """Save progress to disk"""
    try:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('Error saving progress file:', e)

def get_words_to_practice(level):
    """Get words that need practice (status: notyetanswered or incorrect)"""
    if level not in progress_data:
        initialize_missing_levels()
    
    words_to_practice = [
        word for word in progress_data[level] 
        if word['status'] in ['notyetanswered', 'incorrect']
    ]
    return words_to_practice

def update_word_status(level, artikel, deutsch, english, new_status):
    """Update the status of a specific word"""
    if level not in progress_data:
        initialize_missing_levels()
    
    for word in progress_data[level]:
        if (word['artikel'] == artikel and 
            word['deutsch'] == deutsch and 
            word['english'] == english):
            word['status'] = new_status
            save_progress()
            return True
    return False

def get_completion_stats(level):
    """Get completion statistics for a level"""
    if level not in progress_data:
        initialize_missing_levels()
    
    total = len(progress_data[level])
    correct = len([w for w in progress_data[level] if w['status'] == 'correct'])
    incorrect = len([w for w in progress_data[level] if w['status'] == 'incorrect'])
    not_answered = len([w for w in progress_data[level] if w['status'] == 'notyetanswered'])
    
    return {
        'total': total,
        'correct': correct,
        'incorrect': incorrect,
        'not_answered': not_answered,
        'completed': not_answered == 0 and incorrect == 0
    }

# Get distinct levels for filtering
level_options = vocab_df['Level'].dropna().unique()

# Load progress on startup
load_progress()

def update_vocab_csv(english_word, old_german, old_artikel, new_german, new_english, new_artikel, level):
    """Update the vocab.csv file with corrected values"""
    global vocab_df
    try:
        # Create backup of original data
        csv_path = 'data/vocab.csv'
        backup_path = f'data/vocab_backup_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv'
        
        # Check if file is writable
        if not os.access(csv_path, os.W_OK):
            print(f"Error: vocab.csv is not writable")
            return False
            
        # Normalize strings for comparison (strip whitespace and handle multi-line)
        def normalize_string(s):
            if pd.isna(s) or s is None:
                return ''
            # Handle multi-line strings more aggressively
            normalized = str(s).strip()
            normalized = normalized.replace('\n', ' ').replace('\r', ' ')
            normalized = ' '.join(normalized.split())  # Remove extra whitespace
            return normalized
        
        # Special handling for artikel comparison
        def normalize_artikel(s):
            if pd.isna(s) or s is None or str(s).lower().strip() == 'none':
                return ''
            # Handle multi-line strings more aggressively  
            normalized = str(s).strip()
            normalized = normalized.replace('\n', ' ').replace('\r', ' ')
            normalized = ' '.join(normalized.split())  # Remove extra whitespace
            return normalized
        
        # Find the row to update - use normalized strings for matching
        normalized_english = normalize_string(english_word)
        normalized_old_german = normalize_string(old_german)
        normalized_old_artikel = normalize_artikel(old_artikel)
        
        # Create mask with normalized comparisons
        mask = (
            vocab_df['English'].apply(normalize_string) == normalized_english
        ) & (
            vocab_df['Deutsch'].apply(normalize_string) == normalized_old_german
        ) & (
            vocab_df['Artikel'].apply(normalize_artikel) == normalized_old_artikel
        )
        
        print(f"Searching for match:")
        print(f"  English: '{normalized_english}' (original: '{english_word}')")
        print(f"  German: '{normalized_old_german}' (original: '{old_german}')")
        print(f"  Artikel: '{normalized_old_artikel}' (original: '{old_artikel}')")
        
        # Debug: Check if individual fields match
        english_matches = vocab_df['English'].apply(normalize_string) == normalized_english
        german_matches = vocab_df['Deutsch'].apply(normalize_string) == normalized_old_german
        artikel_matches = vocab_df['Artikel'].apply(normalize_artikel) == normalized_old_artikel
        
        print(f"  English matches: {english_matches.sum()} rows")
        print(f"  German matches: {german_matches.sum()} rows") 
        print(f"  Artikel matches: {artikel_matches.sum()} rows")
        print(f"  Combined matches: {mask.sum()} rows")
        
        # Show rows that match English but not others
        if english_matches.any():
            print("Rows matching English:")
            for idx in vocab_df[english_matches].index:
                row = vocab_df.loc[idx]
                print(f"  Row {idx}: English='{normalize_string(row['English'])}', German='{normalize_string(row['Deutsch'])}', Artikel='{normalize_artikel(row['Artikel'])}'")
                print(f"    Raw values: English='{repr(row['English'])}', German='{repr(row['Deutsch'])}', Artikel='{repr(row['Artikel'])}'")
                print(f"    German match: {normalize_string(row['Deutsch']) == normalized_old_german} ('{normalize_string(row['Deutsch'])}' == '{normalized_old_german}')")
                print(f"    Artikel match: {normalize_artikel(row['Artikel']) == normalized_old_artikel} ('{normalize_artikel(row['Artikel'])}' == '{normalized_old_artikel}')")
        
        print(f"  Found {mask.sum()} matching rows")
        
        if mask.any():
            # Create backup before making changes
            vocab_df.to_csv(backup_path, index=False)
            
            # Update the row with new values (only if they're different and provided)
            if new_english and normalize_string(new_english) != normalized_english:
                vocab_df.loc[mask, 'English'] = new_english
                print(f"Updated English: {english_word} -> {new_english}")
            if new_german and normalize_string(new_german) != normalized_old_german:
                vocab_df.loc[mask, 'Deutsch'] = new_german
                print(f"Updated German: {old_german} -> {new_german}")
            if normalize_artikel(new_artikel) != normalized_old_artikel:  # Allow empty string to remove artikel
                vocab_df.loc[mask, 'Artikel'] = new_artikel if new_artikel else None
                print(f"Updated Artikel: {old_artikel} -> {new_artikel}")
                
            # Save back to CSV
            vocab_df.to_csv(csv_path, index=False)
            
            # Update level options in case they changed
            global level_options
            level_options = vocab_df['Level'].dropna().unique()
            
            print(f"Successfully updated vocab entry")
            return True
        else:
            print(f"No matching entry found")
            
            # Let's also try a more flexible search - maybe the issue is with exact matching
            print("\nTrying partial matches:")
            
            # Try finding by English only
            english_only_matches = vocab_df[vocab_df['English'].apply(normalize_string).str.contains(normalized_english, na=False, regex=False)]
            if len(english_only_matches) > 0:
                print("Partial English matches found:")
                for idx, row in english_only_matches.iterrows():
                    print(f"  Row {idx}: '{normalize_string(row['English'])}' vs '{normalized_english}'")
            
            # Try finding by German only  
            german_only_matches = vocab_df[vocab_df['Deutsch'].apply(normalize_string).str.contains(normalized_old_german, na=False, regex=False)]
            if len(german_only_matches) > 0:
                print("Partial German matches found:")
                for idx, row in german_only_matches.iterrows():
                    print(f"  Row {idx}: '{normalize_string(row['Deutsch'])}' vs '{normalized_old_german}'")
            
            # Show some sample data for debugging
            print("\nFirst few rows for reference:")
            for idx, row in vocab_df.head().iterrows():
                print(f"  Row {idx}: English='{normalize_string(row['English'])}', German='{normalize_string(row['Deutsch'])}', Artikel='{normalize_artikel(row['Artikel'])}'")
            
            return False
            
    except Exception as e:
        print(f"Error updating vocab.csv: {e}")
        import traceback
        traceback.print_exc()
        return False

@Deutsch_Vocab_blueprint.route('/Deutsch_Vocab_html', methods=['GET', 'POST'])
def deutsch_vocab():
    try:
        # Get level from form (POST) or query params (GET), default to A1.1
        level = request.form.get('level') or request.args.get('level', 'A1.1')
        
        # Ensure progress is initialized for this level
        if level not in progress_data:
            initialize_missing_levels()
        
        # Filter the dataframe based on the level
        level_vocab = vocab_df[vocab_df['Level'] == level]

        if request.method == 'POST':
            if request.form.get('english_word'):
                # This is a word submission
                english_word = request.form['english_word']
                user_input = request.form['user_input'].strip()
                correct_deutsch = request.form['correct_deutsch'].strip()
                artikel = request.form['artikel'].strip()
                is_retry = request.form.get('is_retry', 'false') == 'true'

                # Check if this is a correction submission
                is_correction = request.form.get('is_correction', 'false') == 'true'
                corrected_german = request.form.get('corrected_german', '').strip()
                corrected_english = request.form.get('corrected_english', '').strip()
                corrected_artikel = request.form.get('corrected_artikel', '').strip()
                
                if is_correction and (corrected_german or corrected_english or corrected_artikel):
                    # Handle CSV correction submission
                    correction_details = {
                        'original': {
                            'german': correct_deutsch,
                            'english': english_word,
                            'artikel': artikel
                        },
                        'corrected': {
                            'german': corrected_german or correct_deutsch,
                            'english': corrected_english or english_word,
                            'artikel': corrected_artikel if corrected_artikel else artikel
                        }
                    }
                    
                    # Update the CSV file with corrections
                    update_success = update_vocab_csv(
                        english_word=english_word,
                        old_german=correct_deutsch,
                        old_artikel=artikel,
                        new_german=corrected_german,
                        new_english=corrected_english,
                        new_artikel=corrected_artikel,
                        level=level
                    )
                    
                    message = 'Thank you! Your corrections have been saved to the database: {}'.format(
                        ', '.join([f"{k}: '{correction_details['corrected'][k]}'" for k in correction_details['corrected'] 
                                 if correction_details['corrected'][k] != correction_details['original'][k]])
                    ) if update_success else 'Your correction was noted but there was an issue updating the file.'
                    
                    return jsonify({
                        'type': 'correction_submitted', 
                        'message': message,
                        'metrics': {
                            'correct': True,
                            'retry': False,
                            'correction': True,
                            'word': correct_deutsch,
                            'english': english_word,
                            'artikel': artikel,
                            'update_success': update_success,
                            'timestamp': datetime.now().isoformat()
                        }
                    })
                
                # Process regular answer submission
                user_clean = user_input.lower().strip()
                correct_clean = correct_deutsch.lower().strip()
                artikel_clean = artikel.lower().strip() if artikel else ''
                correct_with_artikel = f"{artikel_clean} {correct_clean}".strip()
                
                # Check if answer is correct
                is_correct = (user_clean == correct_clean or 
                             user_clean == correct_with_artikel or
                             (artikel_clean and user_clean == f"{correct_clean} {artikel_clean}"))

                if is_correct:
                    # Update word status to 'correct' (only if not practice retry)
                    if not is_retry:
                        # First-time correct answer
                        update_word_status(level, artikel, correct_deutsch, english_word, 'correct')
                        message = 'Correct! The word "{}" is {} {}.'.format(english_word, artikel, correct_deutsch)
                    else:
                        # Practice retry - don't change status, just acknowledge
                        message = 'Good practice! Remember: "{}" is {} {}.'.format(english_word, artikel, correct_deutsch)
                    
                    flash_message = {
                        'type': 'success',
                        'message': message,
                        'metrics': {
                            'correct': True,
                            'retry': is_retry,
                            'word': correct_deutsch,
                            'english': english_word,
                            'artikel': artikel,
                            'timestamp': datetime.now().isoformat()
                        }
                    }
                else:
                    # Incorrect answer
                    if not is_retry:
                        # First attempt wrong - mark as incorrect for spaced repetition
                        update_word_status(level, artikel, correct_deutsch, english_word, 'incorrect')
                        flash_message = {
                            'type': 'retry_with_correction',
                            'message': 'Incorrect. The correct answer is "{} {}". Please practice typing it:'.format(artikel, correct_deutsch),
                            'correct_answer': correct_deutsch,
                            'artikel': artikel,
                            'english_word': english_word,
                            'metrics': {
                                'correct': False,
                                'retry': False,
                                'word': correct_deutsch,
                                'english': english_word,
                                'artikel': artikel,
                                'user_answer': user_input,
                                'timestamp': datetime.now().isoformat()
                            }
                        }
                    else:
                        # Retry still wrong - status already 'incorrect', just acknowledge
                        flash_message = {
                            'type': 'error_with_correction',
                            'message': 'Still incorrect. The correct answer is "{} {}". Try again or suggest a correction.'.format(artikel, correct_deutsch),
                            'correct_answer': correct_deutsch,
                            'artikel': artikel,
                            'english_word': english_word,
                            'user_answer': user_input,
                            'metrics': {
                                'correct': False,
                                'retry': True,
                                'word': correct_deutsch,
                                'english': english_word,
                                'artikel': artikel,
                                'user_answer': user_input,
                                'timestamp': datetime.now().isoformat()
                            }
                        }

                return jsonify(flash_message)
                # This is a word submission
                english_word = request.form['english_word']
                user_input = request.form['user_input'].strip()
                correct_deutsch = request.form['correct_deutsch'].strip()
                artikel = request.form['artikel'].strip()
                is_retry = request.form.get('is_retry', 'false') == 'true'

                # Debug: Print all form data for correction submissions
                if request.form.get('is_correction', 'false') == 'true':
                    print("=== CORRECTION FORM DATA ===")
                    for key, value in request.form.items():
                        print(f"{key}: '{value}'")
                    print("============================")

                # Clean user input and correct answer for comparison
                user_clean = user_input.lower().strip()
                correct_clean = correct_deutsch.lower().strip()
                
                # Check if this is a correction submission
                is_correction = request.form.get('is_correction', 'false') == 'true'
                corrected_german = request.form.get('corrected_german', '').strip()
                corrected_english = request.form.get('corrected_english', '').strip()
                corrected_artikel = request.form.get('corrected_artikel', '').strip()
                
                if is_correction and (corrected_german or corrected_english or corrected_artikel):
                    # Handle full correction submission
                    correction_details = {
                        'original': {
                            'german': correct_deutsch,
                            'english': english_word,
                            'artikel': artikel
                        },
                        'corrected': {
                            'german': corrected_german or correct_deutsch,
                            'english': corrected_english or english_word,
                            'artikel': corrected_artikel if corrected_artikel else artikel
                        }
                    }
                    
                    # Update the CSV file with corrections
                    update_success = update_vocab_csv(
                        english_word=english_word,
                        old_german=correct_deutsch,
                        old_artikel=artikel,
                        new_german=corrected_german,
                        new_english=corrected_english,
                        new_artikel=corrected_artikel,
                        level=level
                    )
                    
                    if update_success:
                        message = 'Thank you! Your corrections have been saved to the database: {}'.format(
                            ', '.join([f"{k}: '{correction_details['corrected'][k]}'" for k in correction_details['corrected'] 
                                     if correction_details['corrected'][k] != correction_details['original'][k]])
                        )
                    else:
                        message = 'Your correction was noted but there was an issue updating the file. Changes: {}'.format(
                            ', '.join([f"{k}: '{correction_details['corrected'][k]}'" for k in correction_details['corrected'] 
                                     if correction_details['corrected'][k] != correction_details['original'][k]])
                        )
                    
                    flash_message = {
                        'type': 'correction_submitted', 
                        'message': message,
                        'metrics': {
                            'correct': True,  # Count corrections as correct for metrics
                            'retry': False,
                            'correction': True,
                            'word': correct_deutsch,
                            'english': english_word,
                            'artikel': artikel,
                            'correction_details': correction_details,
                            'corrected_values': correction_details['corrected'],  # Easy access for frontend
                            'update_success': update_success,
                            'timestamp': datetime.now().isoformat()
                        }
                    }
                    return jsonify(flash_message)
                
                # Allow artikel to be optionally included in the answer
                artikel_clean = artikel.lower().strip() if artikel else ''
                correct_with_artikel = f"{artikel_clean} {correct_clean}".strip()
                
                # Check if answer is correct (with or without artikel)
                is_correct = (user_clean == correct_clean or 
                             user_clean == correct_with_artikel or
                             (artikel_clean and user_clean == f"{correct_clean} {artikel_clean}"))

                if is_correct:
                    word_key = (artikel, correct_deutsch, english_word)
                    
                    # Check if this word was initially wrong (in missed_words)
                    was_initially_wrong = word_key in missed_words
                    
                    if was_initially_wrong:
                        # Remove from missed_words but DON'T count as completed yet
                        # This is just a retry success, not true mastery
                        missed_words.remove(word_key)
                    else:
                        # This word was answered correctly on first try
                        # Add to completed words (persisted per level)
                        completed_list = progress_data.setdefault(level, [])
                        if word_key not in completed_list:
                            completed_list.append(word_key)
                            # persist change
                            save_progress()

                    if is_retry:
                        flash_message = {
                            'type': 'success', 
                            'message': 'Great! You got it right this time. "{}" is {} {}.'.format(english_word, artikel, correct_deutsch),
                            'metrics': {
                                'correct': True,
                                'retry': True,
                                'word': correct_deutsch,
                                'english': english_word,
                                'artikel': artikel,
                                'timestamp': datetime.now().isoformat()
                            }
                        }
                    else:
                        flash_message = {
                            'type': 'success', 
                            'message': 'Correct! The word "{}" is {} {}.'.format(english_word, artikel, correct_deutsch),
                            'metrics': {
                                'correct': True,
                                'retry': False,
                                'word': correct_deutsch,
                                'english': english_word,
                                'artikel': artikel,
                                'timestamp': datetime.now().isoformat()
                            }
                        }
                else:
                    # ANY incorrect answer (first attempt OR retry) adds word to missed_words
                    word_key = (artikel, correct_deutsch, english_word)
                    if word_key not in missed_words:
                        missed_words.append(word_key)
                    
                    if is_retry:
                        # If it's a retry and still wrong, show error with correction option
                        flash_message = {
                            'type': 'error_with_correction', 
                            'message': 'Still incorrect. The database says "{} {}". If you think this is wrong, you can suggest a correction.'.format(artikel, correct_deutsch),
                            'correct_answer': correct_deutsch,
                            'artikel': artikel,
                            'english_word': english_word,
                            'user_answer': user_input,
                            'metrics': {
                                'correct': False,
                                'retry': True,
                                'word': correct_deutsch,
                                'english': english_word,
                                'artikel': artikel,
                                'user_answer': user_input,
                                'timestamp': datetime.now().isoformat()
                            }
                        }
                    else:
                        # First attempt wrong - add to mistakes and show correct answer
                        flash_message = {
                            'type': 'retry_with_correction', 
                            'message': 'Incorrect. The database says "{} {}". Please type it now, or suggest a correction if you think it\'s wrong:'.format(artikel, correct_deutsch),
                            'correct_answer': correct_deutsch,
                            'artikel': artikel,
                            'english_word': english_word,
                            'metrics': {
                                'correct': False,
                                'retry': False,
                                'word': correct_deutsch,
                                'english': english_word,
                                'artikel': artikel,
                                'user_answer': user_input,
                                'timestamp': datetime.now().isoformat()
                            }
                        }

                return jsonify(flash_message)

        # NEW TUPLE-STATE LOGIC: Get words that need practice
        words_to_practice = get_words_to_practice(level)
        
        # Check completion
        stats = get_completion_stats(level)
        if stats['completed']:
            # All words mastered!
            return render_template('Deutsch_Vocab_html.html', 
                                 completed=True, 
                                 level=level, 
                                 total_words=stats['total'],
                                 level_options=level_options)
        
        # Select a random word from words that need practice
        if not words_to_practice:
            return render_template('Deutsch_Vocab_html.html', 
                                 error="No words available for practice.", 
                                 level_options=level_options)
        
        # Pick a random word to practice
        selected_word = random.choice(words_to_practice)
        artikel = selected_word['artikel']
        deutsch_word = selected_word['deutsch'] 
        english_word = selected_word['english']

        # Don't display 'nan' if Artikel is blank
        artikel_display = artikel if artikel else ''

        # Get stats for progress display
        return render_template('Deutsch_Vocab_html.html', 
                             english_word=english_word, 
                             correct_deutsch=deutsch_word, 
                             artikel=artikel_display, 
                             level=level, 
                             level_options=level_options,
                             total_words=stats['total'],
                             completed_words=stats['correct'])

    except Exception as e:
        return render_template('Deutsch_Vocab_html.html', 
                             error="Error occurred: " + str(e), 
                             level_options=level_options)


@Deutsch_Vocab_blueprint.route('/vocab/<level>/progress', methods=['GET'])
def get_level_progress(level):
    """Get progress information for a specific level"""
    try:
        global vocab_df, progress_data
        
        # Filter vocabulary for the specific level
        level_vocab = vocab_df[vocab_df['Level'] == level]
        
        if level_vocab.empty:
            return jsonify({'status': 'error', 'message': 'Level not found'})
        
        # Get completion stats using new system
        stats = get_completion_stats(level)
        
        return jsonify({
            'status': 'success',
            'level': level,
            'total_words': stats['total'],
            'completed_words': stats['correct'],
            'remaining_words': stats['not_answered'] + stats['incorrect'],
            'completion_percentage': round((stats['correct'] / stats['total']) * 100, 1) if stats['total'] > 0 else 0
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@Deutsch_Vocab_blueprint.route('/vocab/<level>/reset', methods=['POST'])
def reset_level_progress(level):
    """Reset progress for a specific level"""
    try:
        # Reset progress for this level by reinitializing
        if level in progress_data:
            # Reset all words for this level to 'notyetanswered'
            for word in progress_data[level]:
                word['status'] = 'notyetanswered'
            save_progress()
        else:
            # Initialize the level if it doesn't exist
            initialize_missing_levels()
        
        return jsonify({
            'status': 'success',
            'message': f'Progress for level {level} has been reset'
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@Deutsch_Vocab_blueprint.route('/vocab/reset-all', methods=['POST'])
def reset_all_progress():
    """Reset all progress"""
    try:
        # Reset all progress by reinitializing everything
        global progress_data
        progress_data = {}
        initialize_missing_levels()
        save_progress()
        
        return jsonify({
            'status': 'success',
            'message': 'All progress has been reset'
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# Initialize progress on startup
load_progress()