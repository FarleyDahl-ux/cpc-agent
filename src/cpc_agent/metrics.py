"""Deterministic metric calculation and signal flagging (data_dictionary.txt
"Field definitions" and "Flagging rules"). No LLM involvement here."""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from . import config


def _num(value: str | None) -> float | None:
    if value is None or value.strip() == "":
        return None
    return float(value)


# --- Calculated metrics --------------------------------------------------


@dataclass
class SatisfactionMetric:
    row_index: int
    reporting_period: str
    delivery_mode: str
    survey_item: str
    pct_satisfied: float
    n_respondents: float
    organisation_avg_pct: float | None
    prev_pct_satisfied: float | None
    prev_n_respondents: float | None

    @property
    def gap_vs_org(self) -> float | None:
        if self.organisation_avg_pct is None:
            return None
        return self.pct_satisfied - self.organisation_avg_pct

    @property
    def gap_vs_prev(self) -> float | None:
        if self.prev_pct_satisfied is None:
            return None
        return self.pct_satisfied - self.prev_pct_satisfied


@dataclass
class SegmentWithdrawalMetric:
    delivery_region: str
    delivery_mode: str
    reporting_period: str
    enrolments: float
    withdrawals: float
    prev_reporting_period: str
    prev_enrolments: float
    prev_withdrawals: float

    @property
    def rate(self) -> float:
        return self.withdrawals / self.enrolments * 100

    @property
    def prev_rate(self) -> float:
        return self.prev_withdrawals / self.prev_enrolments * 100

    @property
    def change_pp(self) -> float:
        return self.rate - self.prev_rate


@dataclass
class UnitWithdrawalMetric:
    unit_code: str
    unit_title: str
    reporting_period: str
    enrolments: float
    withdrawals: float

    @property
    def rate(self) -> float:
        return self.withdrawals / self.enrolments * 100


@dataclass
class MetricsBundle:
    satisfaction: list[SatisfactionMetric] = field(default_factory=list)
    segment_withdrawal: list[SegmentWithdrawalMetric] = field(default_factory=list)
    unit_withdrawal: list[UnitWithdrawalMetric] = field(default_factory=list)
    median_unit_withdrawal_rate: float | None = None


def calculate_metrics(
    course_metrics_rows: list[dict[str, str]],
    satisfaction_rows: list[dict[str, str]],
    unit_rows: list[dict[str, str]],
    excluded_course_segment_keys: set[tuple[str, str, str]] | None = None,
) -> MetricsBundle:
    """Computes every rate the flagging rules need. Rows whose segment key
    (reporting_period, delivery_region, delivery_mode) is in
    excluded_course_segment_keys (e.g. Region C 2025, excluded for a blank
    withdrawals value) are left out of segment withdrawal metrics entirely —
    never estimated, never included in a total."""
    excluded_course_segment_keys = excluded_course_segment_keys or set()

    # --- Satisfaction ---
    prev_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in satisfaction_rows:
        if row.get("reporting_period") == config.PREVIOUS_PERIOD:
            prev_by_key[(row["delivery_mode"], row["survey_item"])] = row

    satisfaction_metrics: list[SatisfactionMetric] = []
    for row_index, row in enumerate(satisfaction_rows):
        if row.get("reporting_period") != config.CURRENT_PERIOD:
            continue
        prev_row = prev_by_key.get((row["delivery_mode"], row["survey_item"]))
        satisfaction_metrics.append(
            SatisfactionMetric(
                row_index=row_index,
                reporting_period=row["reporting_period"],
                delivery_mode=row["delivery_mode"],
                survey_item=row["survey_item"],
                pct_satisfied=_num(row["pct_satisfied"]),
                n_respondents=_num(row["n_respondents"]),
                organisation_avg_pct=_num(row.get("organisation_avg_pct")),
                prev_pct_satisfied=_num(prev_row["pct_satisfied"]) if prev_row else None,
                prev_n_respondents=_num(prev_row["n_respondents"]) if prev_row else None,
            )
        )

    # --- Segment (course) withdrawal ---
    prev_segments: dict[tuple[str, str], dict[str, str]] = {}
    for row in course_metrics_rows:
        if row.get("reporting_period") == config.PREVIOUS_PERIOD:
            prev_segments[(row["delivery_region"], row["delivery_mode"])] = row

    segment_metrics: list[SegmentWithdrawalMetric] = []
    for row in course_metrics_rows:
        if row.get("reporting_period") != config.CURRENT_PERIOD:
            continue
        key = (row["reporting_period"], row["delivery_region"], row["delivery_mode"])
        if key in excluded_course_segment_keys:
            continue
        prev_row = prev_segments.get((row["delivery_region"], row["delivery_mode"]))
        if prev_row is None:
            continue
        segment_metrics.append(
            SegmentWithdrawalMetric(
                delivery_region=row["delivery_region"],
                delivery_mode=row["delivery_mode"],
                reporting_period=row["reporting_period"],
                enrolments=_num(row["enrolments"]),
                withdrawals=_num(row["withdrawals"]),
                prev_reporting_period=prev_row["reporting_period"],
                prev_enrolments=_num(prev_row["enrolments"]),
                prev_withdrawals=_num(prev_row["withdrawals"]),
            )
        )

    # --- Unit withdrawal ---
    unit_metrics: list[UnitWithdrawalMetric] = []
    for row in unit_rows:
        unit_metrics.append(
            UnitWithdrawalMetric(
                unit_code=row["unit_code"],
                unit_title=row["unit_title"],
                reporting_period=row["reporting_period"],
                enrolments=_num(row["enrolments"]),
                withdrawals=_num(row["withdrawals"]),
            )
        )
    median_rate = statistics.median(u.rate for u in unit_metrics) if unit_metrics else None

    return MetricsBundle(
        satisfaction=satisfaction_metrics,
        segment_withdrawal=segment_metrics,
        unit_withdrawal=unit_metrics,
        median_unit_withdrawal_rate=median_rate,
    )


# --- Signal flagging -------------------------------------------------


@dataclass
class Signal:
    id: str
    kind: str  # "satisfaction" | "unit_withdrawal" | "segment_withdrawal"
    measure: str
    segment: str
    value: float
    n: float
    calculation: str
    comparison_rule: str
    source_file: str
    period: str
    sort_key: float
    details: dict = field(default_factory=dict)


def flag_signals(bundle: MetricsBundle) -> list[Signal]:
    """Applies the flagging rules and assigns IDs S1... in the required
    order: satisfaction signals (largest gap first), then unit signals,
    then segment withdrawal signals."""
    satisfaction_signals: list[Signal] = []
    for m in bundle.satisfaction:
        if m.n_respondents < config.SATISFACTION_MIN_N:
            continue
        org_trigger = m.gap_vs_org is not None and m.gap_vs_org <= -config.SATISFACTION_GAP_VS_ORG_PP
        prev_trigger = m.gap_vs_prev is not None and m.gap_vs_prev <= -config.SATISFACTION_GAP_VS_PREV_PP
        if not (org_trigger or prev_trigger):
            continue
        candidate_gaps = [g for g in (m.gap_vs_org, m.gap_vs_prev) if g is not None]
        primary_gap = min(candidate_gaps)
        rule_parts = []
        if org_trigger:
            rule_parts.append(
                f"{abs(m.gap_vs_org):.0f} points below organisation average "
                f"({m.organisation_avg_pct:.0f}%, {config.CURRENT_PERIOD})"
            )
        if prev_trigger:
            rule_parts.append(
                f"{abs(m.gap_vs_prev):.0f} points below {config.PREVIOUS_PERIOD} "
                f"({m.prev_pct_satisfied:.0f}%)"
            )
        comparison_rule = " and ".join(rule_parts) + f"; n={m.n_respondents:.0f} ≥ {config.SATISFACTION_MIN_N}"
        satisfaction_signals.append(
            Signal(
                id="",
                kind="satisfaction",
                measure=m.survey_item,
                segment=m.delivery_mode,
                value=m.pct_satisfied,
                n=m.n_respondents,
                calculation=f"{m.pct_satisfied:.0f}% satisfied (n={m.n_respondents:.0f})",
                comparison_rule=comparison_rule,
                source_file="satisfaction.csv",
                period=config.CURRENT_PERIOD,
                sort_key=primary_gap,
                details={
                    "organisation_avg_pct": m.organisation_avg_pct,
                    "gap_vs_org": m.gap_vs_org,
                    "prev_pct_satisfied": m.prev_pct_satisfied,
                    "gap_vs_prev": m.gap_vs_prev,
                },
            )
        )
    satisfaction_signals.sort(key=lambda s: s.sort_key)

    unit_signals: list[Signal] = []
    if bundle.median_unit_withdrawal_rate is not None:
        median_rate = bundle.median_unit_withdrawal_rate
        for u in bundle.unit_withdrawal:
            if u.rate >= config.UNIT_WITHDRAWAL_RATE_MIN_PCT and u.rate >= config.UNIT_WITHDRAWAL_MEDIAN_MULTIPLIER * median_rate:
                comparison_rule = (
                    f"≥{config.UNIT_WITHDRAWAL_RATE_MIN_PCT:.0f}% and ≥"
                    f"{config.UNIT_WITHDRAWAL_MEDIAN_MULTIPLIER}× median unit withdrawal rate "
                    f"({median_rate:.2f}%)"
                )
                unit_signals.append(
                    Signal(
                        id="",
                        kind="unit_withdrawal",
                        measure="unit_withdrawal_rate",
                        segment=u.unit_code,
                        value=u.rate,
                        n=u.enrolments,
                        calculation=f"{u.withdrawals:.0f} / {u.enrolments:.0f} × 100 = {u.rate:.1f}%",
                        comparison_rule=comparison_rule,
                        source_file="unit_results.csv",
                        period=u.reporting_period,
                        sort_key=-u.rate,
                        details={"unit_title": u.unit_title, "median_unit_withdrawal_rate": median_rate},
                    )
                )
        unit_signals.sort(key=lambda s: s.sort_key)

    segment_signals: list[Signal] = []
    for sgm in bundle.segment_withdrawal:
        if sgm.change_pp >= config.SEGMENT_WITHDRAWAL_RISE_PP:
            comparison_rule = (
                f"risen ≥{config.SEGMENT_WITHDRAWAL_RISE_PP} percentage points on "
                f"{sgm.prev_reporting_period} ({sgm.prev_rate:.1f}% → {sgm.rate:.1f}%)"
            )
            segment_signals.append(
                Signal(
                    id="",
                    kind="segment_withdrawal",
                    measure="segment_withdrawal_rate",
                    segment=sgm.delivery_region,
                    value=sgm.rate,
                    n=sgm.enrolments,
                    calculation=f"{sgm.withdrawals:.0f} / {sgm.enrolments:.0f} × 100 = {sgm.rate:.1f}%",
                    comparison_rule=comparison_rule,
                    source_file="course_metrics.csv",
                    period=sgm.reporting_period,
                    sort_key=-sgm.change_pp,
                    details={
                        "prev_period": sgm.prev_reporting_period,
                        "prev_rate": sgm.prev_rate,
                        "prev_enrolments": sgm.prev_enrolments,
                        "change_pp": sgm.change_pp,
                    },
                )
            )
    segment_signals.sort(key=lambda s: s.sort_key)

    ordered = satisfaction_signals + unit_signals + segment_signals
    for index, signal in enumerate(ordered, start=1):
        signal.id = f"S{index}"
    return ordered
