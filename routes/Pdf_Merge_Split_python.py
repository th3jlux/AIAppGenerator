from flask import Flask, request, render_template, redirect, url_for, flash, send_file, Blueprint
from PyPDF2 import PdfReader, PdfWriter
import os

app = Flask(__name__)
app.secret_key = 'supersecretkey'

Pdf_Merge_Split_blueprint = Blueprint('Pdf_Merge_Split_blueprint', __name__)

UPLOAD_FOLDER = 'uploads/'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

@Pdf_Merge_Split_blueprint.route('/Pdf_Merge_Split_html', methods=['GET', 'POST'])
def pdf_merge_split():
    if request.method == 'POST':
        try:
            # Handling PDF merging
            if 'merge_submit' in request.form:
                files = request.files.getlist('merge_files')
                if not files or files[0].filename == '':
                    flash('No files selected for merging', 'error')
                    return redirect(url_for('Pdf_Merge_Split_blueprint.pdf_merge_split'))
                pdf_writer = PdfWriter()
                for file in files:
                    pdf_reader = PdfReader(file)
                    for page in range(len(pdf_reader.pages)):
                        pdf_writer.add_page(pdf_reader.pages[page])
                output_path = os.path.join(UPLOAD_FOLDER, 'merged.pdf')
                with open(output_path, 'wb') as out_file:
                    pdf_writer.write(out_file)
                flash('PDFs merged successfully!', 'success')
                return send_file(output_path, as_attachment=True)

            # Handling PDF splitting
            elif 'split_submit' in request.form:
                file = request.files['split_file']
                if not file or file.filename == '':
                    flash('No file selected for splitting', 'error')
                    return redirect(url_for('Pdf_Merge_Split_blueprint.pdf_merge_split'))
                start_page = int(request.form['start_page']) - 1
                end_page = int(request.form['end_page'])
                pdf_reader = PdfReader(file)
                if start_page < 0 or end_page > len(pdf_reader.pages):
                    flash('Invalid page range specified', 'error')
                    return redirect(url_for('Pdf_Merge_Split_blueprint.pdf_merge_split'))
                pdf_writer = PdfWriter()
                for page in range(start_page, end_page):
                    pdf_writer.add_page(pdf_reader.pages[page])
                output_path = os.path.join(UPLOAD_FOLDER, 'split.pdf')
                with open(output_path, 'wb') as out_file:
                    pdf_writer.write(out_file)
                flash('PDF split successfully!', 'success')
                return send_file(output_path, as_attachment=True)

        except Exception as e:
            flash(str(e), 'error')
            return redirect(url_for('Pdf_Merge_Split_blueprint.pdf_merge_split'))

    return render_template('Pdf_Merge_Split_html.html')

app.register_blueprint(Pdf_Merge_Split_blueprint)
