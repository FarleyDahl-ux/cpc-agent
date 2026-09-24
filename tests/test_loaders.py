from cpc_agent import config
from cpc_agent.loaders import load_approved_sources


def test_loads_all_required_files_present():
    sources = load_approved_sources(config.DATA_DIR)
    for key in config.REQUIRED_FILES:
        f = sources.get(key)
        assert f.present, f"{f.filename} should be present in data/"
        assert f.rows, f"{f.filename} should have at least one row"
        assert f.sha256, f"{f.filename} should have a content hash"


def test_optional_student_feedback_present_in_sample_data():
    sources = load_approved_sources(config.DATA_DIR)
    fb = sources.get("student_feedback")
    assert fb.present
    assert fb.rows


def test_missing_file_reported_not_raised(tmp_path):
    # An empty data dir: no files present, but loading itself must not raise.
    sources = load_approved_sources(tmp_path)
    for key in config.REQUIRED_FILES:
        assert sources.get(key).present is False
