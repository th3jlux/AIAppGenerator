from flask import Flask, render_template, request, Blueprint, jsonify, session
import random
from datetime import datetime
import os
import json

Deutsch_Vocab_blueprint = Blueprint('Deutsch_Vocab_blueprint', __name__)

# progress_data stores complete vocab state per level with status tracking
# Structure: { "A1.1": [{"artikel": "der", "deutsch": "Hund", "english": "dog", "status": "notyetanswered"}, ...], ... }
progress_data = {}

# Progress file on disk
PROGRESS_FILE = 'data/progress.json'

def load_progress():
    """Load progress from disk - progress.json is the ONLY source of truth"""
    global progress_data
    try:
        if os.path.exists(PROGRESS_FILE):
            with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
                progress_data = json.load(f)
                print(f'Loaded progress data with {len(progress_data)} levels')
        else:
            print('No progress.json file found. Please create one using vocab_to_progress.py script.')
            progress_data = {}
            
    except Exception as e:
        print('Error loading progress file:', e)
        progress_data = {}

def get_level_options():
    """Get available levels from progress data only"""
    return list(progress_data.keys()) if progress_data else []

def save_progress():
    """Save progress to disk"""
    try:
        with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print('Error saving progress file:', e)

def get_words_to_practice(level, difficult_only=False, min_incorrect_count=None):
    """Get words that need practice (status: notyetanswered or incorrect)
    
    Args:
        level: The vocabulary level
        difficult_only: If True, only return words marked as difficult
        min_incorrect_count: If set, only return words with at least this many incorrect attempts
    """
    if level not in progress_data:
        return []
    
    # If min_incorrect_count is specified, include all words with that many errors (even if status is correct)
    # This allows reviewing previously difficult words
    if min_incorrect_count is not None and min_incorrect_count > 0:
        words_to_practice = [
            word for word in progress_data[level]
            if word.get('incorrect_count', 0) >= min_incorrect_count
        ]
    else:
        # Default behavior: only words that still need practice
        words_to_practice = [
            word for word in progress_data[level] 
            if word['status'] in ['notyetanswered', 'incorrect']
        ]
    
    if difficult_only:
        words_to_practice = [
            word for word in words_to_practice
            if word.get('difficulty') == 'hard'
        ]
    
    return words_to_practice

def update_word_status(level, artikel, deutsch, english, new_status):
    """Update the status of a specific word"""
    if level not in progress_data:
        return False
    
    for word in progress_data[level]:
        if (word['artikel'] == artikel and 
            word['deutsch'] == deutsch and 
            word['english'] == english):
            word['status'] = new_status
            save_progress()
            return True
    return False

def increment_incorrect_count(level, artikel, deutsch, english):
    """Increment the incorrect count for a specific word"""
    if level not in progress_data:
        return 0
    
    for word in progress_data[level]:
        if (word['artikel'] == artikel and 
            word['deutsch'] == deutsch and 
            word['english'] == english):
            # Initialize incorrect_count if it doesn't exist (for backwards compatibility)
            if 'incorrect_count' not in word:
                word['incorrect_count'] = 0
            word['incorrect_count'] += 1
            save_progress()
            return word['incorrect_count']
    return 0

def get_word_incorrect_count(level, artikel, deutsch, english):
    """Get the incorrect count for a specific word"""
    if level not in progress_data:
        return 0
    
    for word in progress_data[level]:
        if (word['artikel'] == artikel and 
            word['deutsch'] == deutsch and 
            word['english'] == english):
            return word.get('incorrect_count', 0)
    return 0

def get_most_difficult_words(level, limit=10):
    """Get the words with the highest incorrect counts for a level"""
    if level not in progress_data:
        return []
    
    # Ensure all words have incorrect_count field
    for word in progress_data[level]:
        if 'incorrect_count' not in word:
            word['incorrect_count'] = 0
    
    # Sort words by incorrect_count in descending order
    difficult_words = sorted(
        progress_data[level], 
        key=lambda w: w.get('incorrect_count', 0), 
        reverse=True
    )
    
    # Filter out words with 0 incorrect count
    difficult_words = [w for w in difficult_words if w.get('incorrect_count', 0) > 0]
    
    return difficult_words[:limit]

def reset_word_incorrect_count(level, artikel, deutsch, english):
    """Reset the incorrect count for a specific word"""
    if level not in progress_data:
        return False
    
    for word in progress_data[level]:
        if (word['artikel'] == artikel and 
            word['deutsch'] == deutsch and 
            word['english'] == english):
            word['incorrect_count'] = 0
            save_progress()
            return True
    return False

def mark_word_difficulty(level, artikel, deutsch, english, is_difficult):
    """Mark a word as difficult or easy"""
    if level not in progress_data:
        return False
    
    for word in progress_data[level]:
        if (word['artikel'] == artikel and 
            word['deutsch'] == deutsch and 
            word['english'] == english):
            if is_difficult:
                word['difficulty'] = 'hard'
            else:
                # Remove difficulty field if marking as easy
                word.pop('difficulty', None)
            save_progress()
            return True
    return False

def sync_progress_with_vocab_change(level, old_english, old_german, old_artikel, new_english, new_german, new_artikel):
    """Sync progress.json when vocab.csv changes for a specific word"""
    if level not in progress_data:
        return False
    
    # Find the word in progress data using old values
    for word in progress_data[level]:
        if (word['artikel'] == old_artikel and 
            word['deutsch'] == old_german and 
            word['english'] == old_english):
            
            # Update the word with new values (preserve status and difficulty)
            if new_english:
                word['english'] = new_english
            if new_german:
                word['deutsch'] = new_german
            if new_artikel is not None:  # Allow empty string to remove artikel
                word['artikel'] = new_artikel
                
            print(f"Synced progress entry: {old_english}/{old_german}/{old_artikel} -> {word['english']}/{word['deutsch']}/{word['artikel']}")
            save_progress()
            return True
    
    print(f"No progress entry found to sync for: {old_english}/{old_german}/{old_artikel}")
    return False

def get_completion_stats(level):
    """Get completion statistics for a level"""
    if level not in progress_data:
        return {'total': 0, 'correct': 0, 'incorrect': 0, 'not_answered': 0, 'completion_percentage': 0}
    
    
    # Ensure all words have incorrect_count field (backwards compatibility)
    for word in progress_data[level]:
        if 'incorrect_count' not in word:
            word['incorrect_count'] = 0
    
    total = len(progress_data[level])
    correct = len([w for w in progress_data[level] if w['status'] == 'correct'])
    incorrect = len([w for w in progress_data[level] if w['status'] == 'incorrect'])
    not_answered = len([w for w in progress_data[level] if w['status'] == 'notyetanswered'])
    
    # Calculate stats about incorrect counts
    total_incorrect_attempts = sum(w.get('incorrect_count', 0) for w in progress_data[level])
    words_with_errors = len([w for w in progress_data[level] if w.get('incorrect_count', 0) > 0])
    most_difficult_count = max([w.get('incorrect_count', 0) for w in progress_data[level]], default=0)
    
    return {
        'total': total,
        'correct': correct,
        'incorrect': incorrect,
        'not_answered': not_answered,
        'completed': not_answered == 0 and incorrect == 0,
        'total_incorrect_attempts': total_incorrect_attempts,
        'words_with_errors': words_with_errors,
        'most_difficult_count': most_difficult_count
    }

# Get distinct levels from progress data
level_options = []

# Load progress on startup
load_progress()

# Get level options from loaded progress data
level_options = get_level_options()

# Note: update_vocab_csv function removed - no longer using vocab.csv for updates
# Use separate vocab_to_progress.py script to convert vocab.csv to progress.json if needed

@Deutsch_Vocab_blueprint.route('/Deutsch_Vocab_html', methods=['GET', 'POST'])
def deutsch_vocab():
    try:
        # Get levels from form (POST) or query params (GET)
        # Support both single level (backwards compatibility) and multiple levels
        if request.method == 'POST':
            # Handle multiple levels from form
            selected_levels = request.form.getlist('levels')  # Get list of selected levels
            if not selected_levels:
                # Fallback to single level for backwards compatibility
                single_level = request.form.get('level', 'A1.1')
                selected_levels = [single_level] if single_level else ['A1.1']
        else:
            # Handle multiple levels from query parameters
            levels_param = request.args.getlist('levels')  # Use getlist for multiple 'levels' parameters
            if levels_param:
                selected_levels = levels_param
            else:
                # Check for comma-separated levels in a single parameter
                levels_param = request.args.get('levels', '')
                if levels_param:
                    # Multiple levels separated by comma: ?levels=A1.1,A1.2,B1.1
                    selected_levels = [level.strip() for level in levels_param.split(',') if level.strip()]
                else:
                    # Single level for backwards compatibility
                    single_level = request.args.get('level', 'A1.1')
                    selected_levels = [single_level]
        
        # Ensure at least one level is selected
        if not selected_levels:
            selected_levels = ['A1.1']
        
        # Filter to valid levels only
        selected_levels = [level for level in selected_levels if level in level_options]
        if not selected_levels:
            selected_levels = ['A1.1']  # Fallback
        
        # Get toggle states from form or query params
        difficult_only = (request.form.get('difficult_only', 'false').lower() == 'true' or 
                         request.args.get('difficult_only', 'false') == 'true')
        articles_mandatory = (request.form.get('articles_mandatory', 'false').lower() == 'true' or
                            request.args.get('articles_mandatory', 'false') == 'true')
        
        # Get incorrect_count filter (minimum number of incorrect attempts)
        min_incorrect_count = None
        try:
            min_incorrect_str = request.form.get('min_incorrect_count') or request.args.get('min_incorrect_count', '')
            if min_incorrect_str.strip():
                min_incorrect_count = int(min_incorrect_str.strip())
                if min_incorrect_count < 0:
                    min_incorrect_count = None
        except (ValueError, TypeError):
            min_incorrect_count = None
        
        # Check if levels exist in progress data
        for level in selected_levels:
            if level not in progress_data:
                return render_template('errors.html', error_msg=f"Level '{level}' not found in progress data. Please ensure progress.json is properly initialized.")

        if request.method == 'POST':
            if request.form.get('english_word'):
                # This is a word submission - get the specific level for this word
                word_level = request.form.get('word_level', selected_levels[0])  # Track which level this word came from
                english_word = request.form['english_word']
                user_input = request.form['user_input'].strip()
                correct_deutsch = request.form['correct_deutsch'].strip()
                artikel = request.form['artikel'].strip()
                is_retry = request.form.get('is_retry', 'false') == 'true'
                
                # Handle pre-marked difficulty if present
                pre_difficulty = request.form.get('pre_difficulty')
                if pre_difficulty is not None:
                    is_difficult = pre_difficulty.lower() == 'true'
                    mark_word_difficulty(word_level, artikel, correct_deutsch, english_word, is_difficult)

                # Check if this is a correction submission
                is_correction = request.form.get('is_correction', 'false') == 'true'
                corrected_german = request.form.get('corrected_german', '').strip()
                corrected_english = request.form.get('corrected_english', '').strip()
                corrected_artikel = request.form.get('corrected_artikel', '').strip()
                corrected_beispielsatz = request.form.get('corrected_beispielsatz', '').strip()
                
                if is_correction and (corrected_german or corrected_english or corrected_artikel or corrected_beispielsatz):
                    # Get current beispielsatz for comparison
                    current_beispielsatz = request.form.get('beispielsatz', '').strip()
                    
                    # Handle CSV correction submission
                    correction_details = {
                        'original': {
                            'german': correct_deutsch,
                            'english': english_word,
                            'artikel': artikel,
                            'beispielsatz': current_beispielsatz
                        },
                        'corrected': {
                            'german': corrected_german or correct_deutsch,
                            'english': corrected_english or english_word,
                            'artikel': corrected_artikel if corrected_artikel else artikel,
                            'beispielsatz': corrected_beispielsatz if corrected_beispielsatz else current_beispielsatz
                        }
                    }
                    
                    # Note: CSV updates no longer performed - progress.json is the source of truth
                    update_success = True  # Always succeed since we're not updating CSV
                    
                    message = 'Thank you! Your corrections have been saved to the database: {}'.format(
                        ', '.join([f"{k}: '{correction_details['corrected'][k]}'" for k in correction_details['corrected'] 
                                 if correction_details['corrected'][k] != correction_details['original'][k]])
                    ) if update_success else 'Your correction was noted but there was an issue updating the file.'
                    
                    # Try to fetch an example sentence (Beispielsatz) for this word
                    beispielsatz = ''
                    try:
                        if 'Beispielsatz' in vocab_df.columns:
                            mask = (vocab_df['Level'] == word_level) & (vocab_df['Deutsch'] == correct_deutsch)
                            artik_series = vocab_df['Artikel'].fillna('').astype(str)
                            mask = mask & (artik_series == (artikel if artikel else ''))
                            row = vocab_df[mask]
                            if not row.empty:
                                beispielsatz_val = row.iloc[0].get('Beispielsatz')
                                if beispielsatz_val is not None and str(beispielsatz_val).strip():
                                    beispielsatz = str(beispielsatz_val)
                    except Exception:
                        beispielsatz = ''

                    return jsonify({
                        'type': 'correction_submitted', 
                        'message': message,
                        'beispielsatz': beispielsatz,
                        'metrics': {
                            'correct': True,
                            'retry': False,
                            'correction': True,
                            'word': correct_deutsch,
                            'english': english_word,
                            'artikel': artikel,
                            'level': word_level,
                            'update_success': update_success,
                            'timestamp': datetime.now().isoformat()
                        }
                    })
                
                # Process regular answer submission
                user_clean = user_input.lower().strip()
                correct_clean = correct_deutsch.lower().strip()
                artikel_clean = artikel.lower().strip() if artikel else ''
                correct_with_artikel = f"{artikel_clean} {correct_clean}".strip()
                
                # Check if articles are mandatory
                articles_mandatory = request.form.get('articles_mandatory', 'false') == 'true'
                
                # Check if answer is correct
                if articles_mandatory and artikel_clean:
                    # When articles are mandatory and word has article, require article
                    is_correct = (user_clean == correct_with_artikel or
                                user_clean == f"{correct_clean} {artikel_clean}")
                else:
                    # Normal mode: accept with or without article
                    is_correct = (user_clean == correct_clean or 
                                 user_clean == correct_with_artikel or
                                 (artikel_clean and user_clean == f"{correct_clean} {artikel_clean}"))

                # Try to fetch an example sentence (Beispielsatz) for this word
                beispielsatz = ''
                try:
                    if 'Beispielsatz' in vocab_df.columns:
                        mask = (vocab_df['Level'] == word_level) & (vocab_df['Deutsch'] == correct_deutsch)
                        artik_series = vocab_df['Artikel'].fillna('').astype(str)
                        mask = mask & (artik_series == (artikel if artikel else ''))
                        row = vocab_df[mask]
                        if not row.empty:
                            beispielsatz_val = row.iloc[0].get('Beispielsatz')
                            if beispielsatz_val is not None and str(beispielsatz_val).strip():
                                beispielsatz = str(beispielsatz_val)
                except Exception:
                    beispielsatz = ''

                if is_correct:
                    # Update word status to 'correct' (only if not practice retry)
                    if not is_retry:
                        # First-time correct answer
                        update_word_status(word_level, artikel, correct_deutsch, english_word, 'correct')
                        message = 'Correct! The word "{}" is {} {}. (Level: {})'.format(english_word, artikel, correct_deutsch, word_level)
                    else:
                        # Practice retry - don't change status, just acknowledge
                        message = 'Good practice! Remember: "{}" is {} {}. (Level: {})'.format(english_word, artikel, correct_deutsch, word_level)
                    
                    flash_message = {
                        'type': 'success',
                        'message': message,
                        'beispielsatz': beispielsatz,
                        'metrics': {
                            'correct': True,
                            'retry': is_retry,
                            'word': correct_deutsch,
                            'english': english_word,
                            'artikel': artikel,
                            'level': word_level,
                            'timestamp': datetime.now().isoformat()
                        }
                    }
                else:
                    # Incorrect answer
                    if not is_retry:
                        # First attempt wrong - mark as incorrect for spaced repetition and increment count
                        update_word_status(word_level, artikel, correct_deutsch, english_word, 'incorrect')
                        incorrect_count = increment_incorrect_count(word_level, artikel, correct_deutsch, english_word)
                        
                        flash_message = {
                            'type': 'retry_with_correction',
                            'message': 'Incorrect. The correct answer is "{} {}". Please practice typing it: (Level: {})'.format(artikel, correct_deutsch, word_level),
                            'correct_answer': correct_deutsch,
                            'artikel': artikel,
                            'beispielsatz': beispielsatz,
                            'english_word': english_word,
                            'metrics': {
                                'correct': False,
                                'retry': False,
                                'word': correct_deutsch,
                                'english': english_word,
                                'artikel': artikel,
                                'level': word_level,
                                'user_answer': user_input,
                                'incorrect_count': incorrect_count,
                                'timestamp': datetime.now().isoformat()
                            }
                        }
                    else:
                        # Retry still wrong - increment count again (they're still struggling with this word)
                        incorrect_count = increment_incorrect_count(word_level, artikel, correct_deutsch, english_word)
                        
                        flash_message = {
                            'type': 'error_with_correction',
                            'message': 'Still incorrect. The correct answer is "{} {}". Try again or suggest a correction. (Level: {})'.format(artikel, correct_deutsch, word_level),
                            'correct_answer': correct_deutsch,
                            'artikel': artikel,
                            'beispielsatz': beispielsatz,
                            'english_word': english_word,
                            'user_answer': user_input,
                            'metrics': {
                                'correct': False,
                                'retry': True,
                                'word': correct_deutsch,
                                'english': english_word,
                                'artikel': artikel,
                                'level': word_level,
                                'user_answer': user_input,
                                'incorrect_count': incorrect_count,
                                'timestamp': datetime.now().isoformat()
                            }
                        }

                return jsonify(flash_message)
            # Handle difficulty marking
            elif request.form.get('mark_difficulty'):
                word_level = request.form.get('word_level', selected_levels[0])
                artikel = request.form.get('artikel', '').strip()
                deutsch = request.form.get('deutsch', '').strip()
                english = request.form.get('english', '').strip()
                difficulty = request.form.get('difficulty') == 'true'
                
                success = mark_word_difficulty(word_level, artikel, deutsch, english, difficulty)
                if success:
                    status = 'difficult' if difficulty else 'normal'
                    return jsonify({
                        'type': 'difficulty_updated',
                        'message': f'Word marked as {status}',
                        'difficulty': difficulty
                    })
                else:
                    return jsonify({
                        'type': 'error',
                        'message': 'Failed to update difficulty'
                    })

        # NEW TUPLE-STATE LOGIC: Get words that need practice from all selected levels
        # Aggregate words from all selected levels
        all_words_to_practice = []
        all_stats = {'total': 0, 'correct': 0, 'incorrect': 0, 'not_answered': 0, 'completed': True}
        
        for level in selected_levels:
            words_for_level = get_words_to_practice(level, difficult_only, min_incorrect_count)
            # Add level information to each word
            for word in words_for_level:
                word['level'] = level  # Track which level each word comes from
            all_words_to_practice.extend(words_for_level)
            
            # Aggregate statistics
            level_stats = get_completion_stats(level)
            all_stats['total'] += level_stats['total']
            all_stats['correct'] += level_stats['correct']
            all_stats['incorrect'] += level_stats['incorrect']
            all_stats['not_answered'] += level_stats['not_answered']
            # Only consider fully completed if ALL selected levels are completed
            if not level_stats['completed']:
                all_stats['completed'] = False
        
        # Check completion for all selected levels
        if all_stats['completed']:
            # All words in all selected levels mastered!
            return render_template('Deutsch_Vocab_html.html', 
                                 completed=True, 
                                 selected_levels=selected_levels,  # Pass list of levels
                                 levels_display=', '.join(selected_levels),  # For display
                                 total_words=all_stats['total'],
                                 level_options=level_options,
                                 difficult_only=difficult_only,
                                 articles_mandatory=articles_mandatory,
                                 min_incorrect_count=min_incorrect_count)
        
        # Select a random word from words that need practice
        if not all_words_to_practice:
            if min_incorrect_count is not None:
                error_msg = f"No words with {min_incorrect_count}+ incorrect attempts found in selected levels. Try a lower number or remove the filter."
            elif difficult_only:
                error_msg = "No difficult words available for practice in selected levels."
            else:
                error_msg = "No words available for practice in selected levels. All words completed!"
            return render_template('Deutsch_Vocab_html.html', 
                                 error=error_msg, 
                                 selected_levels=selected_levels,
                                 levels_display=', '.join(selected_levels),
                                 level_options=level_options,
                                 difficult_only=difficult_only,
                                 articles_mandatory=articles_mandatory,
                                 min_incorrect_count=min_incorrect_count)
        
        # Pick a random word to practice
        selected_word = random.choice(all_words_to_practice)
        artikel = selected_word['artikel']
        deutsch_word = selected_word['deutsch'] 
        english_word = selected_word['english']
        word_level = selected_word['level']  # Get the specific level this word is from

        # Don't display 'nan' if Artikel is blank
        artikel_display = artikel if artikel else ''
        
        # Get the Beispielsatz for this word from the CSV
        beispielsatz = ''
        try:
            if 'Beispielsatz' in vocab_df.columns:
                mask = (vocab_df['Level'] == word_level) & (vocab_df['Deutsch'] == deutsch_word)
                artik_series = vocab_df['Artikel'].fillna('').astype(str)
                mask = mask & (artik_series == (artikel if artikel else ''))
                row = vocab_df[mask]
                if not row.empty:
                    beispielsatz_val = row.iloc[0].get('Beispielsatz')
                    if beispielsatz_val is not None and str(beispielsatz_val).strip():
                        beispielsatz = str(beispielsatz_val)
        except Exception:
            beispielsatz = ''

        # Get stats for progress display
        return render_template('Deutsch_Vocab_html.html', 
                             english_word=english_word, 
                             correct_deutsch=deutsch_word, 
                             artikel=artikel_display,
                             beispielsatz=beispielsatz,  # Pass current Beispielsatz
                             word_level=word_level,  # Pass the specific level this word is from
                             selected_levels=selected_levels,  # Pass list of selected levels
                             levels_display=', '.join(selected_levels),  # For display
                             level_options=level_options,
                             total_words=all_stats['total'],
                             completed_words=all_stats['correct'],
                             difficult_only=difficult_only,
                             articles_mandatory=articles_mandatory,
                             min_incorrect_count=min_incorrect_count)

    except Exception as e:
        return render_template('Deutsch_Vocab_html.html', 
                             error="Error occurred: " + str(e), 
                             selected_levels=['A1.1'],  # Default fallback
                             levels_display='A1.1',
                             level_options=level_options,
                             difficult_only=False,
                             articles_mandatory=False,
                             min_incorrect_count=None)


@Deutsch_Vocab_blueprint.route('/vocab/progress', methods=['GET'])
def get_progress():
    """Get progress information for levels with filtering support"""
    try:
        global vocab_df, progress_data
        
        # Parse multiple levels from query parameters
        levels_param = request.args.getlist('levels')  # Use getlist for multiple 'levels' parameters
        if not levels_param:
            # Check for comma-separated levels in a single parameter
            levels_str = request.args.get('levels', '')
            if levels_str:
                levels_param = [level.strip() for level in levels_str.split(',') if level.strip()]
            else:
                # Single level for backwards compatibility
                single_level = request.args.get('level', '')
                levels_param = [single_level] if single_level else []
        
        # Filter to valid levels only
        selected_levels = [level for level in levels_param if level in level_options]
        if not selected_levels:
            return jsonify({'status': 'error', 'message': 'No valid levels provided'})
        
        # Get filtering parameters
        difficult_only = request.args.get('difficult_only', 'false') == 'true'
        min_incorrect_count = None
        try:
            min_incorrect_str = request.args.get('min_incorrect_count', '')
            if min_incorrect_str.strip():
                min_incorrect_count = int(min_incorrect_str.strip())
                if min_incorrect_count < 0:
                    min_incorrect_count = None
        except (ValueError, TypeError):
            min_incorrect_count = None
        
        # Aggregate words from all selected levels with filtering
        all_words = []
        total_words_available = 0
        completed_words = 0
        
        for level in selected_levels:
            # Get all words that match the filtering criteria
            words_for_level = get_words_to_practice(level, difficult_only, min_incorrect_count)
            all_words.extend(words_for_level)
            
            # For total count, we need to count words that match the filter, not just those needing practice
            if level not in progress_data:
                return jsonify({'total': 0, 'correct': 0, 'incorrect': 0, 'not_answered': 0, 'completion_percentage': 0})
            
            if difficult_only:
                # Count only words marked as difficult
                level_words = [w for w in progress_data[level] if w.get('difficulty') == 'hard']
            elif min_incorrect_count is not None and min_incorrect_count > 0:
                # Count words with at least the specified incorrect count
                level_words = [w for w in progress_data[level] if w.get('incorrect_count', 0) >= min_incorrect_count]
            else:
                # Count all words in the level
                level_words = progress_data[level]
            
            total_words_available += len(level_words)
            
            # Count completed words from this level that match the filter
            completed_level_words = [w for w in level_words if w['status'] == 'correct']
            completed_words += len(completed_level_words)
        
        # Calculate remaining words (words that still need practice)
        remaining_words = len(all_words)
        
        return jsonify({
            'status': 'success',
            'levels': selected_levels,
            'total_words': total_words_available,
            'completed_words': completed_words,
            'remaining_words': remaining_words,
            'completion_percentage': round((completed_words / total_words_available) * 100, 1) if total_words_available > 0 else 0,
            'filters': {
                'difficult_only': difficult_only,
                'min_incorrect_count': min_incorrect_count
            }
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@Deutsch_Vocab_blueprint.route('/vocab/<level>/progress', methods=['GET'])
def get_level_progress(level):
    """Get progress information for a specific level (backwards compatibility)"""
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
            # Reset all words for this level to 'notyetanswered' and reset incorrect counts
            for word in progress_data[level]:
                word['status'] = 'notyetanswered'
                word['incorrect_count'] = 0
            save_progress()
        else:
            # Return error if level doesn't exist in progress data
            return jsonify({'error': f"Level '{level}' not found in progress data"}), 404
        
        return jsonify({
            'status': 'success',
            'message': f'Progress for level {level} has been reset'
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@Deutsch_Vocab_blueprint.route('/vocab/reset-all', methods=['POST'])
def reset_all_progress():
    """Reset all progress - set all words back to 'notyetanswered' status with 0 incorrect count"""
    try:
        global progress_data
        
        # Check if progress data exists
        if not progress_data:
            return jsonify({
                'status': 'error', 
                'message': 'No progress data found. Please ensure progress.json is properly initialized using vocab_to_progress.py script.'
            })
        
        # Reset all words to initial state instead of clearing everything
        reset_count = 0
        for level, words in progress_data.items():
            for word in words:
                word['status'] = 'notyetanswered'
                word['incorrect_count'] = 0
                # Remove difficulty marking if it exists
                word.pop('difficulty', None)
                reset_count += 1
        
        save_progress()
        
        return jsonify({
            'status': 'success',
            'message': f'All progress has been reset. {reset_count} words reset across {len(progress_data)} levels.'
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

# Initialize progress on startup
load_progress()


@Deutsch_Vocab_blueprint.route('/vocab/<level>/difficult-words', methods=['GET'])
def get_difficult_words(level):
    """Get the most difficult words (highest incorrect counts) for a level"""
    try:
        limit = int(request.args.get('limit', 10))
        difficult_words = get_most_difficult_words(level, limit)
        
        return jsonify({
            'status': 'success',
            'level': level,
            'difficult_words': difficult_words,
            'count': len(difficult_words)
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@Deutsch_Vocab_blueprint.route('/vocab/dashboard-sync', methods=['GET'])
def sync_dashboard():
    """Sync dashboard metrics with actual progress.json data"""
    try:
        # Get all levels to calculate overall stats
        all_levels = level_options
        
        # Calculate actual metrics from progress.json
        total_words_practiced = 0
        correct_answers = 0
        incorrect_answers = 0
        recent_words = []
        
        # Aggregate stats from all levels
        for level in all_levels:
            if level not in progress_data:
                continue
                
            level_words = progress_data[level]
            
            for word in level_words:
                # Count words that have been practiced (not 'notyetanswered')
                if word['status'] != 'notyetanswered':
                    total_words_practiced += 1
                    
                    if word['status'] == 'correct':
                        correct_answers += 1
                    elif word['status'] == 'incorrect':
                        incorrect_answers += 1
                
                # Add to recent words if it has been practiced
                if word['status'] != 'notyetanswered':
                    recent_words.append({
                        'word': word['deutsch'],
                        'english': word['english'],
                        'correct': word['status'] == 'correct',
                        'retry': False,  # We can't determine this from progress.json
                        'level': level,
                        'incorrect_count': word.get('incorrect_count', 0)
                    })
        
        # Sort recent words by incorrect count (most difficult first) and take last 10
        recent_words = sorted(recent_words, key=lambda x: x['incorrect_count'], reverse=True)[:10]
        
        return jsonify({
            'status': 'success',
            'dashboard_data': {
                'totalWords': total_words_practiced,
                'correctAnswers': correct_answers,
                'incorrectAnswers': incorrect_answers,
                'recentWords': recent_words,
                'currentStreak': 0,  # Reset streak since we can't determine from progress.json
                'bestStreak': 0,     # Reset streak since we can't determine from progress.json
                'wordsToday': 0,     # Reset daily count
                'lastSessionDate': None
            }
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@Deutsch_Vocab_blueprint.route('/vocab/<level>/stats', methods=['GET'])
def get_level_stats(level):
    """Get detailed statistics for a level including incorrect counts"""
    try:
        stats = get_completion_stats(level)
        difficult_words = get_most_difficult_words(level, 5)  # Top 5 most difficult
        
        return jsonify({
            'status': 'success',
            'level': level,
            'stats': stats,
            'top_difficult_words': difficult_words
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})


@Deutsch_Vocab_blueprint.route('/vocab/<level>/reset-incorrect-counts', methods=['POST'])
def reset_level_incorrect_counts(level):
    """Reset all incorrect counts for a level"""
    try:
        if level not in progress_data:
            return jsonify({'status': 'error', 'message': 'Level not found'})
        
        # Reset incorrect_count for all words in the level
        for word in progress_data[level]:
            word['incorrect_count'] = 0
        
        save_progress()
        
        return jsonify({
            'status': 'success',
            'message': f'All incorrect counts for level {level} have been reset'
        })
        
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})