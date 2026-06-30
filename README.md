# Multi-Source Candidate Data Transformer

A high-performance, NLP-powered Extract, Transform, Load (ETL) pipeline for parsing resumes and candidate profiles. Built for scalability, this pipeline can parse thousands of resumes in parallel, extract complex entities (skills, experience, education), resolve merge conflicts, and output clean schema-projected JSON.

## Features
- **Parallel Batch Processing**: Process thousands of resumes concurrently.
- **Advanced NLP Extraction**: Uses `spaCy` to deeply understand unstructured text, extracting skills, standardizing dates, and normalizing locations.
- **Smart Conflict Resolution**: Identifies duplicate candidate uploads and seamlessly merges their data based on source reliability.
- **Data Provenance & Confidence**: Explains *where* data was found and calculates a mathematical confidence score (0.0 to 1.0) for every profile.
- **Database Persistence**: Stores all parsed canonical records in PostgreSQL safely with race-condition handling.

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Srinidhi-08/candidate_transformer.git
   cd candidate_transformer
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   python -m spacy download en_core_web_sm
   ```

3. **Database Configuration:**
   Ensure PostgreSQL is running. The pipeline will automatically create the required tables. You can override credentials via environment variables:
   `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`

## Exact Run Steps

**1. Process a single resume:**
```bash
python candidate_pipeline.py "resume.pdf"
```

**2. Process a single resume (Clean JSON Output Only):**
```bash
python candidate_pipeline.py "resume.pdf" --quiet
```

**3. Process a massive directory of resumes in parallel (8 workers) and save to file:**
```bash
python candidate_pipeline.py --dir "./data/resumes" --out "results.json" --workers 8 --quiet
```

**4. Run tests:**
```bash
pytest tests/
```

## Assumptions and Descoped Features

- **Assumptions**: 
  - English is the primary language of the resumes (relies on `en_core_web_sm`).
  - PostgreSQL is the designated target for database persistence. 
  - Candidates are identified uniquely by their parsed emails and phone numbers.

- **Descoped**:
  - **OCR for Images**: The `PdfParser` is designed for text-based PDFs. Image-based PDFs and photos (JPEGs) were descoped for this MVP; integrating Tesseract OCR would be the next step.
  - **Cloud Deployment API**: The tool is currently a CLI. A FastAPI web wrapper was descoped in favor of optimizing the core extraction engine.

## Demo Video
[Insert Demo Video Link Here]
