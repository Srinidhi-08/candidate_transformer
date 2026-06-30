"""
test_pipeline.py
================
Unit & Integration Tests for the Candidate Transformer Pipeline.
Run with: pytest test_pipeline.py
"""

import pytest
from pathlib import Path
from datetime import datetime, timezone
import json

from pipeline import Pipeline
from src.core.models import InputBundle
from src.core.config_loader import get_config
from src.phase1.module1_input import InputCollector
from src.phase1.module2_validator import InputValidationPipeline
from src.phase1.module3_source import SourceIdentificationPipeline
from src.phase1.module4_parsers import ParserSelectionPipeline
from src.phase1.module6_builder import CandidateObjectBuilder, SectionSplitter
from src.phase2.module10_normalizer import Normalizer


@pytest.fixture
def temp_txt_file(tmp_path):
    p = tmp_path / "resume.txt"
    content = """Jane Doe
jane.doe@example.com | (555) 123-4567 | github.com/janedoe

SUMMARY
Experienced Software Engineer specializing in Python and backend services.

EXPERIENCE
Senior Software Engineer @ Google
2020-01 to present
- Led development of key backend REST APIs.
- Worked with Python, Docker, and Kubernetes.

EDUCATION
Bachelor of Science in Computer Science
Stanford University | 2016 - 2020

SKILLS
Python, Docker, Kubernetes, SQL, AWS
"""
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture
def temp_json_file(tmp_path):
    p = tmp_path / "candidate.json"
    data = {
        "name": "John Smith",
        "email": "john.smith@gmail.com",
        "phone": "+1-234-567-8901",
        "linkedin_url": "https://linkedin.com/in/johnsmith",
        "skills": "python, javascript, react",
        "company": "Amazon",
        "headline": "Software Engineer II"
    }
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_module1_input_collection():
    collector = InputCollector()
    bundle = collector.collect(
        file_path="some_file.pdf",
        upload_channel="resume_upload",
        source_hint="From LinkedIn profile",
        referral_code="REF123",
        referred_by="jdoe"
    )
    assert bundle.file_name == "some_file.pdf"
    assert bundle.upload_channel == "resume_upload"
    assert bundle.referral_code == "REF123"
    assert bundle.referred_by == "jdoe"


def test_module2_validator_txt(temp_txt_file):
    collector = InputCollector()
    bundle = collector.collect(file_path=str(temp_txt_file))
    
    validator = InputValidationPipeline()
    meta = validator.run(bundle)
    
    assert meta.validation_status is True
    assert meta.detected_file_type == "txt"
    assert meta.data_category == "unstructured"


def test_module3_source_detection(temp_txt_file):
    collector = InputCollector()
    bundle = collector.collect(
        file_path=str(temp_txt_file),
        upload_channel="linkedin"
    )
    source_pipeline = SourceIdentificationPipeline()
    src_meta, ref_info = source_pipeline.run(bundle, "txt")
    
    assert src_meta.source_type == "linkedin"
    assert src_meta.confidence == 1.0


def test_module4_parser_txt(temp_txt_file):
    validator = InputValidationPipeline()
    bundle = InputCollector().collect(file_path=str(temp_txt_file))
    meta = validator.run(bundle)
    
    parser_pipeline = ParserSelectionPipeline()
    parsed = parser_pipeline.run(meta)
    
    assert parsed.data_category == "unstructured"
    assert "jane.doe@example.com" in parsed.content


def test_section_splitter():
    text = "Jane Doe\n\nEXPERIENCE\nWorked at Google\n\nEDUCATION\nStanford University"
    cfg = get_config()
    splitter = SectionSplitter(cfg)
    sections = splitter.split(text)
    
    assert "experience" in sections
    assert "education" in sections
    assert "Google" in sections["experience"][0]


def test_module10_normalizer():
    normalizer = Normalizer()
    assert normalizer._normalize_date("January 2020") == "2020-01"
    assert normalizer._normalize_date("2021-05-12") == "2021-05"
    assert normalizer._normalize_date("Present") == "present"
    assert normalizer._normalize_degree("B.Tech in CS") == "Bachelor of Technology"
    assert normalizer._normalize_url("github.com/test") == "https://github.com/test"


def test_full_pipeline_txt(temp_txt_file):
    # Run with persist=False to avoid requiring running PG database
    pipeline = Pipeline(persist=False)
    output = pipeline.run(
        file_path=str(temp_txt_file),
        upload_channel="resume_upload",
        schema="minimal"
    )
    
    assert output["full_name"] == "Jane Doe"
    assert "jane.doe@example.com" in output["emails"]
    assert "links.linkedin" in output or "linkedin" in output or any("linkedin" in str(k) for k in output.keys())


def test_full_pipeline_json(temp_json_file):
    pipeline = Pipeline(persist=False)
    output = pipeline.run(
        file_path=str(temp_json_file),
        upload_channel="csv_bulk_import",
        schema="ats"
    )
    
    assert output["name"] == "John Smith"
    assert "john.smith@gmail.com" in output["email"]
    assert len(output["skills"]) == 3
