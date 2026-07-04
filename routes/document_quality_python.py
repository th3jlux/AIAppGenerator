from flask import Blueprint, render_template, request, jsonify
from werkzeug.utils import secure_filename
import os
import re

# Define the document_quality blueprint

document_quality_blueprint = Blueprint('document_quality_blueprint', __name__)

# Maximum file upload size (in bytes)
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

# Heuristic function to analyze document quality
def analyze_document_quality(text):
    try:
        sentences = re.split(r'[.!?]', text)
        sentence_lengths = [len(sentence.split()) for sentence in sentences if sentence]
        max_sentence_length = max(sentence_lengths, default=0)
        average_sentence_length = sum(sentence_lengths) / len(sentence_lengths) if sentence_lengths else 0

        return {
            'max_sentence_length': max_sentence_length,
            'average_sentence_length': average_sentence_length,
            'total_sentences': len(sentences) - 1  # -1 because of trailing split
        }
    except Exception as e:
        raise ValueError("Error analyzing document quality: " + str(e))


@document_quality_blueprint.route('/document_quality_html', methods=['GET', 'POST'])

def document_quality():
    if request.method == 'POST':
        if 'document' not in request.files:
            return jsonify({'error': 'No file part in the request'}), 400

        file = request.files['document']
        if file.filename == '':
            return jsonify({'error': 'No file selected for uploading'}), 400

        if not file.filename.lower().endswith(('.txt', '.docx')):
            return jsonify({'error': 'Unsupported file type. Please upload a .txt or .docx file'}), 400

        try:
            if file and file.filename.lower().endswith('.txt'):
                text = file.read().decode('utf-8')
            else:
                from docx import Document
                document = Document(file)
                text = "\n".join([paragraph.text for paragraph in document.paragraphs])

            analysis = analyze_document_quality(text)
            return jsonify(analysis), 200

        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    return render_template('document_quality_html.html')

# Ensure secure uploads and restrict content length
@document_quality_blueprint.before_request
def before_request():
    request.max_content_length = MAX_CONTENT_LENGTH
    