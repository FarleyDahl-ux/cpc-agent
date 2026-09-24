import json

import pytest

from cpc_agent import config
from cpc_agent.evidence import (
    LIMITED,
    MIXED,
    NO_QUALITATIVE_EVIDENCE,
    SUFFICIENT,
    assign_evidence_status,
    build_evidence_record,
)
from cpc_agent.loaders import load_approved_sources
from cpc_agent.metrics import calculate_metrics, flag_signals

EXPECTED = json.loads((config.BASE_DIR / "tests" / "fixtures" / "expected_results.json").read_text())
REGION_C_EXCLUDED = {("2025", "Region C", "Blended")}


@pytest.mark.parametrize(
    "aligned,countervailing,expected_status",
    [
        (["FB003", "FB007", "FB011", "FB015"], ["FB005", "FB019"], MIXED),
        (["FB002", "FB009", "FB013"], ["FB017"], MIXED),
        ([], [], NO_QUALITATIVE_EVIDENCE),
        (["FB024"], [], LIMITED),
        (["A", "B", "C"], [], SUFFICIENT),
        (["A"], [], LIMITED),
    ],
)
def test_assign_evidence_status_precedence(aligned, countervailing, expected_status):
    assert assign_evidence_status(aligned, countervailing) == expected_status


def test_review_required_takes_precedence_over_everything():
    status = assign_evidence_status(["A", "B", "C"], [], review_required_reason="unresolved privacy condition")
    assert status == "Review required"


@pytest.fixture
def signals_by_measure_segment():
    sources = load_approved_sources(config.DATA_DIR)
    bundle = calculate_metrics(
        sources.get("course_metrics").rows,
        sources.get("satisfaction").rows,
        sources.get("unit_results").rows,
        excluded_course_segment_keys=REGION_C_EXCLUDED,
    )
    signals = flag_signals(bundle)
    return {(s.measure, s.segment): s for s in signals}


def test_evidence_status_matches_expected_for_each_signal(signals_by_measure_segment):
    for sig_id, expected in EXPECTED["signals"].items():
        signal = signals_by_measure_segment[(expected["measure"], expected["segment"])]
        record = build_evidence_record(
            signal,
            aligned_ids=expected["aligned"],
            countervailing_ids=expected["countervailing"],
            themes=["General/unclassifiable"],
        )
        assert record.evidence_status == expected["status"], sig_id
        assert record.finding_id == signal.id


def test_evidence_record_never_carries_quarantined_ids(signals_by_measure_segment):
    signal = signals_by_measure_segment[("teacher_support", "Online")]
    record = build_evidence_record(signal, aligned_ids=["FB003"], countervailing_ids=[], themes=["Teacher responsiveness and support"])
    assert "FB016" not in record.aligned_ids
    assert "FB018" not in record.aligned_ids
