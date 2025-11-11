from flask import Flask, render_template, request, Blueprint, jsonify, session
import pandas as pd
import random
from datetime import datetime
import os

Deutsch_Vocab_blueprint = Blueprint('Deutsch_Vocab_blueprint', __name__)

# Read vocabulary data
try:
    vocab_df = pd.read_csv('data/vocab.csv')
except Exception as e:
    vocab_df = pd.DataFrame(columns=['Artikel', 'Deutsch', 'English', 'Level'])
    print('Error loading vocabulary file:', e)

# Global variables
missed_words = []
current_word_queue = []
completed_words = []  # Track words that have been completed successfully

# Get distinct levels for filtering
level_options = vocab_df['Level'].dropna().unique()

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
    global missed_words, current_word_queue, completed_words
    
    try:
        # Get level from form (POST) or query params (GET), default to A1.1
        level = request.form.get('level') or request.args.get('level', 'A1.1')
        
        # Filter the dataframe based on the level
        level_vocab = vocab_df[vocab_df['Level'] == level]

        if request.method == 'POST':
            # Check if this is a level change request
            if request.form.get('level') and not request.form.get('english_word'):
                # This is a level change, reset word queues
                missed_words = []
                current_word_queue = []
                completed_words = []
            elif request.form.get('english_word'):
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
                    # Add to completed words
                    word_key = (artikel, correct_deutsch, english_word)
                    if word_key not in completed_words:
                        completed_words.append(word_key)
                    
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
                        missed_words.append((artikel, correct_deutsch, english_word))
                    else:
                        # First attempt wrong - show correct answer with correction option
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

        # Initialize word queue for this level if empty
        if not current_word_queue:
            word_set = list(zip(level_vocab['Artikel'], level_vocab['Deutsch'], level_vocab['English']))
            random.shuffle(word_set)
            current_word_queue = word_set.copy()

        # Check if we've completed all words for this level
        total_words_for_level = len(list(zip(level_vocab['Artikel'], level_vocab['Deutsch'], level_vocab['English'])))
        if len(completed_words) >= total_words_for_level and not missed_words:
            # All words completed!
            return render_template('Deutsch_Vocab_html.html', 
                                 completed=True, 
                                 level=level, 
                                 total_words=total_words_for_level,
                                 level_options=level_options)

        # Get next word (prioritize missed words, but add them to end of queue)
        if missed_words:
            artikel, deutsch_word, english_word = missed_words.pop(0)
        else:
            if not current_word_queue:
                # Check completion again before refilling
                if len(completed_words) >= total_words_for_level:
                    return render_template('Deutsch_Vocab_html.html', 
                                         completed=True, 
                                         level=level, 
                                         total_words=total_words_for_level,
                                         level_options=level_options)
                
                # Still have uncompleted words, refill queue with only uncompleted ones
                all_words = list(zip(level_vocab['Artikel'], level_vocab['Deutsch'], level_vocab['English']))
                uncompleted_words = [word for word in all_words if word not in completed_words]
                
                if not uncompleted_words:
                    # Shouldn't happen, but safety check
                    return render_template('Deutsch_Vocab_html.html', 
                                         completed=True, 
                                         level=level, 
                                         total_words=total_words_for_level,
                                         level_options=level_options)
                
                random.shuffle(uncompleted_words)
                current_word_queue = uncompleted_words.copy()
            
            if not current_word_queue:
                return render_template('Deutsch_Vocab_html.html', error="No words available for the selected level.", level_options=level_options)
            
            artikel, deutsch_word, english_word = current_word_queue.pop(0)

        # Don't display 'nan' if Artikel is blank
        artikel_display = artikel if pd.notna(artikel) else ''

        return render_template('Deutsch_Vocab_html.html', english_word=english_word, correct_deutsch=deutsch_word, artikel=artikel_display, level=level, level_options=level_options)

    except Exception as e:
        return render_template('Deutsch_Vocab_html.html', error="Error occurred: " + str(e), level_options=level_options)


@Deutsch_Vocab_blueprint.route('/vocab/<level>/progress', methods=['GET'])
def get_level_progress(level):
    """Get progress information for a specific level"""
    try:
        global vocab_df, completed_words
        
        # Filter vocabulary for the specific level
        level_vocab = vocab_df[vocab_df['Level'] == level]
        
        if level_vocab.empty:
            return jsonify({'status': 'error', 'message': 'Level not found'})
        
        # Calculate totals
        total_words = len(level_vocab)
        completed_words_count = len(completed_words)
        
        # Filter completed words to only those that belong to current level
        level_word_tuples = set(zip(
            level_vocab['Artikel'].fillna(''), 
            level_vocab['Deutsch'], 
            level_vocab['English']
        ))
        
        completed_for_level = len([word for word in completed_words if word in level_word_tuples])
        
        return jsonify({
            'status': 'success',
            'level': level,
            'total_words': total_words,
            'completed_words': completed_for_level,
            'remaining_words': total_words - completed_for_level,
            'completion_percentage': round((completed_for_level / total_words) * 100, 1) if total_words > 0 else 0
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@Deutsch_Vocab_blueprint.route('/vocab/<level>/reset', methods=['POST'])
def reset_level_progress(level):
    """Reset progress for a specific level"""
    try:
        global completed_words, current_word_queue, missed_words
        
        # Filter vocabulary for the specific level to get word tuples
        level_vocab = vocab_df[vocab_df['Level'] == level]
        
        if level_vocab.empty:
            return jsonify({'status': 'error', 'message': 'Level not found'})
        
        # Create set of word tuples for this level
        level_word_tuples = set(zip(
            level_vocab['Artikel'].fillna(''), 
            level_vocab['Deutsch'], 
            level_vocab['English']
        ))
        
        # Remove completed words for this level only
        completed_words = [word for word in completed_words if word not in level_word_tuples]
        
        # Clear current queues (they'll be regenerated when needed)
        current_word_queue = []
        missed_words = []
        
        return jsonify({
            'status': 'success',
            'message': f'Progress reset for level {level}',
            'level': level
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@Deutsch_Vocab_blueprint.route('/vocab/reset-all', methods=['POST'])
def reset_all_progress():
    """Reset all progress"""
    try:
        global completed_words, current_word_queue, missed_words
        
        # Reset all progress tracking
        completed_words = []
        current_word_queue = []
        missed_words = []
        
        return jsonify({
            'status': 'success',
            'message': 'All progress has been reset'
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})