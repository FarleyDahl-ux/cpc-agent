import json

import pytest

from cpc_agent import config
from cpc_agent.loaders import load_approved_sources
from cpc_agent.metrics import calculate_metrics, flag_signals

EXPECTED = json.loads((config.BASE_DIR / "tests" / "fixtures" / "expected_results.json").read_text())

# Region C 2025 has a blank withdrawals value (the missing-value trap). The
# analyst decision under test is "exclude" — mirrors examples/demo_answers.json.
REGION_C_EXCLUDED = {("2025", "Region C", "Blended")}


@pytest.fixture
def signals():
    sources = load_approved_sources(config.DATA_DIR)
    bundle = calculate_metrics(
        sources.get("course_metrics").rows,
        sources.get("satisfaction").rows,
        sources.get("unit_results").rows,
        excluded_course_segment_keys=REGION_C_EXCLUDED,
    )
    return flag_signals(bundle)


def test_four_signals_flagged(signals):
    assert len(signals) == 4
    assert [s.id for s in signals] == ["S1", "S2", "S3", "S4"]


def test_signal_ids_order_and_identity_match_expected(signals):
    by_id = {s.id: s for s in signals}
    for sig_id, expected in EXPECTED["signals"].items():
        signal = by_id[sig_id]
        assert signal.measure == expected["measure"], sig_id
        assert signal.segment == expected["segment"], sig_id


def test_signal_values_and_n_match_expected(signals):
    by_id = {s.id: s for s in signals}
    for sig_id, expected in EXPECTED["signals"].items():
        signal = by_id[sig_id]
        assert signal.value == pytest.approx(expected["value"], abs=0.05), sig_id
        assert signal.n == pytest.approx(expected["n"], abs=0.5), sig_id


def test_no_unseeded_signal_flagged(signals):
    flagged = {(s.measure, s.segment) for s in signals}
    assert ("segment_withdrawal_rate", "Region A") not in flagged
    assert ("segment_withdrawal_rate", "Region B") not in flagged
    assert ("segment_withdrawal_rate", "Region C") not in flagged
    for item in ("teacher_support", "assessment_clarity", "reasonable_workload", "content_relevance", "overall_course"):
        assert (item, "Face to face") not in flagged
        assert (item, "Blended") not in flagged
    assert ("reasonable_workload", "Online") not in flagged
    assert ("content_relevance", "Online") not in flagged
    assert ("overall_course", "Online") not in flagged
    for unit_code in ("CWSU101", "CWSU102", "CWSU103", "CWSU201", "CWSU202", "CWSU203", "CWSU205"):
        assert ("unit_withdrawal_rate", unit_code) not in flagged


def test_excluded_segment_never_estimated_as_75():
    sources = load_approved_sources(config.DATA_DIR)
    bundle = calculate_metrics(
        sources.get("course_metrics").rows,
        sources.get("satisfaction").rows,
        sources.get("unit_results").rows,
        excluded_course_segment_keys=REGION_C_EXCLUDED,
    )
    region_c_present = [m for m in bundle.segment_withdrawal if m.delivery_region == "Region C"]
    assert region_c_present == []
    for m in bundle.segment_withdrawal:
        assert m.withdrawals != 75
        assert m.rate != 75


def test_median_unit_withdrawal_rate():
    sources = load_approved_sources(config.DATA_DIR)
    bundle = calculate_metrics(
        sources.get("course_metrics").rows,
        sources.get("satisfaction").rows,
        sources.get("unit_results").rows,
        excluded_course_segment_keys=REGION_C_EXCLUDED,
    )
    assert bundle.median_unit_withdrawal_rate == pytest.approx(12.49, abs=0.01)
