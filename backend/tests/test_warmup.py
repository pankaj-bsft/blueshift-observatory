"""
Tests for the Deliverability Warm-up Planner.

This is the first test suite in this codebase -- there was no test
infrastructure to extend, so this introduces pytest fresh (see
requirements.txt). Run with: cd backend && ../venv/bin/pytest tests/ -v

Each test gets its own isolated warmup.db via the BS_DATA_DIR env var
(set before either warmup_service or warmup_decision_engine is imported,
since both resolve their DB path at import time) -- never touches real
data, and tests never depend on execution order except where a test
explicitly builds a multi-day sequence itself.
"""
import os
import sys
import sqlite3
import tempfile
import shutil
import importlib

import pytest

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)


@pytest.fixture()
def warmup(monkeypatch):
    """Fresh warmup_service + warmup_decision_engine bound to a throwaway DB."""
    tmp_dir = tempfile.mkdtemp()
    monkeypatch.setenv('BS_DATA_DIR', tmp_dir)

    # warmup_decision_engine lazily imports gpt_service/spamhaus_service
    # (and spamhaus_service transitively imports account_mapping_service)
    # INSIDE its signal-check functions, so those modules' DB-path constants
    # get computed once at first import and cached in sys.modules -- without
    # resetting them here too, every test after the first one silently reads
    # against the FIRST test's already-deleted tmp_dir instead of its own.
    for mod in (
        'data_paths', 'warmup_service', 'warmup_decision_engine', 'warmup_thresholds',
        'gpt_service', 'spamhaus_service', 'account_mapping_service', 'pulsation_service',
    ):
        sys.modules.pop(mod, None)

    import warmup_service as wsvc
    import warmup_decision_engine as engine
    import warmup_thresholds as cfg

    yield wsvc, engine, cfg

    shutil.rmtree(tmp_dir, ignore_errors=True)


# A domain with real, valid SPF/DMARC (used throughout this project's own
# manual testing) -- needed so the authentication check in the decision
# engine doesn't force PAUSE regardless of the scenario under test.
REAL_DOMAIN = 'alert.yespark.com'


def make_plan(wsvc, domain=REAL_DOMAIN, **overrides):
    # isp_mix is absolute daily target volume per ISP (not percentages) --
    # these sum to 50,000, in the same 45/25/15/5/10 proportions the old
    # percentage-based fixture used, so existing assertions about overall
    # shape still hold. target_volume is derived server-side from this and
    # is never read from the payload -- not included here deliberately.
    data = {
        'name': 'test warmup', 'warming_type': 'new_domain', 'esp': 'Mailgun', 'domain': domain,
        'current_volume': 0, 'start_date': '2026-09-29',
        'target_date': '2026-10-20', 'strategy': 'balanced',
        'isp_mix': {'gmail': 22500, 'microsoft': 12500, 'yahoo': 7500, 'apple': 2500, 'comcast': 0, 'other': 5000},
        'audience_quality': 'highly_engaged',
    }
    data.update(overrides)
    return wsvc.create_plan(data)


# --------------------------------------------------------------------------
# Curve generation
# --------------------------------------------------------------------------

class TestCurveGeneration:
    def test_schedule_lands_exactly_on_target_date(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        schedule = wsvc.get_schedule(plan['id'])
        assert schedule[-1]['scheduled_date'] == plan['target_date']
        assert schedule[-1]['planned_total'] == plan['target_volume']

    def test_isp_split_always_sums_to_total(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        for row in wsvc.get_schedule(plan['id']):
            total = (row['planned_gmail'] + row['planned_microsoft'] + row['planned_yahoo']
                     + row['planned_apple'] + row['planned_comcast'] + row['planned_other'])
            assert total == row['planned_total'], f"day {row['day_number']}: {total} != {row['planned_total']}"

    def test_volume_is_monotonically_non_decreasing(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        schedule = wsvc.get_schedule(plan['id'])
        volumes = [row['planned_total'] for row in schedule]
        assert volumes == sorted(volumes)

    def test_infeasible_deadline_extends_plan_instead_of_over_ramping(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(
            wsvc, domain='tight.example', current_volume=0,
            isp_mix={'gmail': 1000000, 'microsoft': 0, 'yahoo': 0, 'apple': 0, 'comcast': 0, 'other': 0},
            start_date='2026-09-29', target_date='2026-10-06', strategy='conservative',
        )
        assert plan['duration_extended'] is True
        assert plan['target_volume'] == 1000000
        schedule = wsvc.get_schedule(plan['id'])
        assert schedule[-1]['planned_total'] == 1000000
        # Every day's growth rate must respect the conservative cap -- allow
        # a small tolerance since planned_total is rounded to a "nice" step
        # each day (e.g. 500 -> 600), which can nudge the ratio a fraction
        # above the exact underlying rate.
        for prev, curr in zip(schedule, schedule[1:]):
            if prev['planned_total'] > 0:
                growth = (curr['planned_total'] - prev['planned_total']) / prev['planned_total']
                assert growth <= cfg.STRATEGY_MAX_DAILY_GROWTH['conservative'] + 0.05

    def test_already_at_target_volume_produces_single_flat_day(self, warmup):
        wsvc, engine, cfg = warmup
        # current_volume (60,000) already exceeds the isp_mix target (50,000
        # total) -- every ISP's starting floor should clamp to its own
        # target immediately, collapsing the ramp to a single flat day.
        plan = make_plan(wsvc, current_volume=60000)
        schedule = wsvc.get_schedule(plan['id'])
        assert len(schedule) == 1
        assert schedule[0]['planned_total'] == 50000


class TestValidation:
    def test_isp_mix_rejects_all_zero(self, warmup):
        wsvc, engine, cfg = warmup
        with pytest.raises(ValueError, match='at least one ISP'):
            make_plan(wsvc, isp_mix={'gmail': 0, 'microsoft': 0, 'yahoo': 0, 'apple': 0, 'comcast': 0, 'other': 0})

    def test_isp_mix_rejects_negative_values(self, warmup):
        wsvc, engine, cfg = warmup
        with pytest.raises(ValueError, match='not be negative'):
            make_plan(wsvc, isp_mix={'gmail': -100, 'microsoft': 0, 'yahoo': 0, 'apple': 0, 'comcast': 0, 'other': 0})

    def test_isp_mix_rejects_unknown_bucket(self, warmup):
        wsvc, engine, cfg = warmup
        with pytest.raises(ValueError, match='unknown buckets'):
            make_plan(wsvc, isp_mix={'gmail': 50, 'aol': 50})

    def test_target_volume_is_derived_not_accepted_from_caller(self, warmup):
        """A caller-supplied target_volume is ignored -- it's always the sum of isp_mix."""
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc, target_volume=999999999)
        assert plan['target_volume'] == 50000

    def test_target_date_must_be_after_start_date(self, warmup):
        wsvc, engine, cfg = warmup
        with pytest.raises(ValueError, match='target_date must be after'):
            make_plan(wsvc, start_date='2026-10-20', target_date='2026-09-29')

    def test_missing_required_fields_rejected(self, warmup):
        wsvc, engine, cfg = warmup
        with pytest.raises(ValueError, match='name'):
            wsvc.create_plan({'domain': 'x.example', 'warming_type': 'new_domain',
                               'target_volume': 100, 'target_date': '2026-10-20',
                               'isp_mix': {'gmail': 100, 'microsoft': 0, 'yahoo': 0, 'apple': 0, 'other': 0}})


# --------------------------------------------------------------------------
# Decision engine -- these mirror the exact scenarios from the product spec
# --------------------------------------------------------------------------

class TestDecisionEngine:
    def test_no_actuals_yet_returns_no_decision(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        result = engine.evaluate(plan['id'], '2026-09-29')
        assert result['decision'] is None

    def test_scenario_everything_healthy_increases(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.record_actual(plan['id'], '2026-09-29', {
            'actual_total': 495, 'delivery_rate': 98.5, 'hard_bounce_rate': 0.3,
            'soft_bounce_rate': 0.5, 'spam_rate': 0.02, 'unsub_rate': 0.1,
        })
        result = engine.evaluate(plan['id'], '2026-09-29')
        assert result['decision'] == 'increase'
        assert result['health_band'] == 'Healthy'

    def test_scenario_warning_level_soft_bounce_holds(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.record_actual(plan['id'], '2026-09-29', {
            'actual_total': 495, 'delivery_rate': 97.5, 'hard_bounce_rate': 0.3,
            'soft_bounce_rate': 1.8, 'spam_rate': 0.02, 'unsub_rate': 0.1,
        })
        result = engine.evaluate(plan['id'], '2026-09-29')
        assert result['decision'] == 'hold'
        assert any(r['signal'] == 'soft_bounce_rate' for r in result['reasons'])

    def test_scenario_single_day_spam_spike_reduces_not_pauses(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 495, 'delivery_rate': 97.0, 'spam_rate': 0.35})
        result = engine.evaluate(plan['id'], '2026-09-29')
        assert result['decision'] == 'reduce'

    def test_scenario_sustained_two_day_critical_pauses(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 495, 'delivery_rate': 97.0, 'spam_rate': 0.35})
        day1 = engine.evaluate(plan['id'], '2026-09-29')
        wsvc.record_actual(plan['id'], '2026-09-30', {'actual_total': 600, 'delivery_rate': 96.5, 'spam_rate': 0.4})
        day2 = engine.evaluate(plan['id'], '2026-09-30')
        assert day1['decision'] == 'reduce'
        assert day2['decision'] == 'pause'

    def test_scenario_spamhaus_listing_forces_pause(self, warmup, monkeypatch):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 495, 'delivery_rate': 98.5, 'spam_rate': 0.02})
        monkeypatch.setattr(engine, '_check_spamhaus',
                             lambda domain: {'signal': 'spamhaus', 'severity': 'critical',
                                              'message': f'{domain} is listed.', 'value': 'listed', 'threshold': None})
        result = engine.evaluate(plan['id'], '2026-09-29')
        assert result['decision'] == 'pause'

    def test_scenario_missing_authentication_forces_pause(self, warmup, monkeypatch):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 495, 'delivery_rate': 98.5, 'spam_rate': 0.02})
        monkeypatch.setattr(engine, '_check_authentication',
                             lambda domain: {'signal': 'spf_missing', 'severity': 'critical',
                                              'message': f'{domain} has no SPF record.', 'value': None, 'threshold': None})
        result = engine.evaluate(plan['id'], '2026-09-29')
        assert result['decision'] == 'pause'

    def test_volume_overshoot_flagged_even_when_other_signals_healthy(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        # planned day 1 is 500; 40% over is well past VOLUME_OVERSHOOT_RISK_PCT (35%)
        wsvc.record_actual(plan['id'], '2026-09-29', {
            'actual_total': 700, 'delivery_rate': 98.5, 'hard_bounce_rate': 0.3,
            'soft_bounce_rate': 0.5, 'spam_rate': 0.02, 'unsub_rate': 0.1,
        })
        result = engine.evaluate(plan['id'], '2026-09-29')
        assert any(r['signal'] == 'volume_overshoot' for r in result['reasons'])
        assert result['decision'] in ('reduce', 'pause')

    def test_reasons_and_explanation_are_never_fabricated_when_no_data(self, warmup):
        """Signals with no data (None) must never appear as breached reasons."""
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 495})  # only volume, nothing else
        result = engine.evaluate(plan['id'], '2026-09-29')
        signals_flagged = {r['signal'] for r in result['reasons']}
        assert 'delivery_rate' not in signals_flagged
        assert 'spam_rate' not in signals_flagged


# --------------------------------------------------------------------------
# Actuals / CSV import
# --------------------------------------------------------------------------

class TestActuals:
    def test_record_actual_is_idempotent_per_date(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 100})
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 200})
        actuals = wsvc.get_actuals(plan['id'])
        assert len(actuals) == 1
        assert actuals[0]['actual_total'] == 200

    def test_csv_import_upserts_and_reports_row_count(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        csv_file = tmp_path / 'actuals.csv'
        csv_file.write_text(
            'actual_date,actual_total,delivery_rate\n'
            '2026-09-29,490,98.5\n'
            '2026-09-30,615,97.9\n'
        )
        result = wsvc.import_actuals_csv(plan['id'], str(csv_file))
        assert result['imported'] == 2
        assert result['errors'] == []
        assert len(wsvc.get_actuals(plan['id'])) == 2

    def test_csv_import_reports_errors_without_crashing(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        csv_file = tmp_path / 'bad.csv'
        csv_file.write_text('actual_date,actual_total\nnot-a-date,100\n2026-09-29,not-a-number\n')
        result = wsvc.import_actuals_csv(plan['id'], str(csv_file))
        assert result['imported'] == 0
        assert len(result['errors']) == 2

    def test_recalculate_all_evaluates_every_actual_date_chronologically(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        # Insert out of order to confirm recalculate sorts chronologically
        wsvc.record_actual(plan['id'], '2026-09-30', {'actual_total': 600, 'delivery_rate': 96.5, 'spam_rate': 0.4})
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 495, 'delivery_rate': 97.0, 'spam_rate': 0.35})
        results = engine.recalculate_all(plan['id'])
        assert [r['date'] for r in results] == ['2026-09-29', '2026-09-30']
        assert results[0]['decision'] == 'reduce'
        assert results[1]['decision'] == 'pause'  # sustained from day 1


class TestNiceRoundingAndPerIspCurves:
    """Covers the two things added when this moved from one blended curve
    split by percentage to independent per-ISP curves: nice-number rounding
    and each ISP landing on its own absolute target."""

    def test_round_nice_matches_reported_example(self, warmup):
        wsvc, engine, cfg = warmup
        assert wsvc._round_nice(612) == 600  # the exact example this was built to fix
        assert wsvc._round_nice(47) == 50
        assert wsvc._round_nice(0) == 0

    def test_schedule_values_are_always_nice_rounded(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        for row in wsvc.get_schedule(plan['id']):
            for isp in cfg.ISP_BUCKETS:
                value = row[f'planned_{isp}']
                assert value == wsvc._round_nice(value), f"day {row['day_number']} {isp}={value} isn't nice-rounded"

    def test_each_isp_lands_exactly_on_its_own_target(self, warmup):
        wsvc, engine, cfg = warmup
        isp_mix = {'gmail': 22500, 'microsoft': 12500, 'yahoo': 7500, 'apple': 2500, 'comcast': 0, 'other': 5000}
        plan = make_plan(wsvc, isp_mix=isp_mix)
        last_day = wsvc.get_schedule(plan['id'])[-1]
        for isp, target in isp_mix.items():
            assert last_day[f'planned_{isp}'] == target

    def test_untargeted_isp_stays_zero_every_day(self, warmup):
        """Comcast (isp_mix=0) should never appear in the ramp -- the
        screenshot this was modeled on leaves it blank throughout."""
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)  # fixture's isp_mix has comcast: 0
        for row in wsvc.get_schedule(plan['id']):
            assert row['planned_comcast'] == 0

    def test_isps_ramp_independently_not_by_fixed_percentage_split(self, warmup):
        """Two ISPs with very different start/target ratios should NOT be
        forced through the same percentage split of a single blended curve --
        each should reach its own target by the same final day regardless."""
        wsvc, engine, cfg = warmup
        isp_mix = {'gmail': 100000, 'microsoft': 1000, 'yahoo': 0, 'apple': 0, 'comcast': 0, 'other': 0}
        plan = make_plan(wsvc, isp_mix=isp_mix, target_date='2026-11-15')
        schedule = wsvc.get_schedule(plan['id'])
        assert schedule[-1]['planned_gmail'] == 100000
        assert schedule[-1]['planned_microsoft'] == 1000
        # Microsoft's target is tiny relative to Gmail's -- it should reach
        # its own target well before the final day, not track a fixed ratio
        # to Gmail's still-climbing number the whole way through.
        first_ms_at_target = next(i for i, r in enumerate(schedule) if r['planned_microsoft'] == 1000)
        assert first_ms_at_target < len(schedule) - 1


# --------------------------------------------------------------------------
# Actual progress (Pulsation-backed, not elapsed calendar time)
# --------------------------------------------------------------------------

def _seed_pulsation_sent(domain, rows):
    """Insert real Pulsation daily_metrics rows for a domain -- rows is a
    list of (report_date, sent) tuples. Mirrors a tiny slice of what
    pulsation_service.insert_daily_data() would actually write."""
    import pulsation_service as psvc
    psvc.init_pulsation_database()
    conn = sqlite3.connect(psvc.DB_PATH)
    cursor = conn.cursor()
    for report_date, sent in rows:
        cursor.execute('''
            INSERT INTO daily_metrics (report_date, from_domain, region, esp, sent, delivered)
            VALUES (?, ?, 'US', 'Mailgun', ?, ?)
        ''', (report_date, domain.lower(), sent, sent))
    conn.commit()
    conn.close()


class TestActualProgress:
    def test_progress_is_not_tracked_when_pulsation_has_no_data(self, warmup):
        """A brand new plan with zero real sends is 'not tracked', not 0% --
        same convention as Pulsation's own unique-open/click columns."""
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc, start_date='2026-09-01', target_date='2026-09-20')
        fetched = wsvc.get_plan(plan['id'])
        assert fetched['progress_pct'] is None
        assert fetched['actual_sent_total'] == 0

    def test_progress_pct_is_actual_sent_over_target_volume(self, warmup):
        """Progress must reflect real Pulsation sends, not elapsed calendar
        time -- this is the exact bug report that prompted the feature."""
        wsvc, engine, cfg = warmup
        domain = 'progress-test.example.com'
        plan = make_plan(wsvc, domain=domain, start_date='2026-09-01', target_date='2026-09-20')
        # target_volume for the default isp_mix is 50,000 (see make_plan)
        _seed_pulsation_sent(domain, [('2026-09-01', 5000), ('2026-09-02', 7500)])
        fetched = wsvc.get_plan(plan['id'])
        assert fetched['actual_sent_total'] == 12500
        assert fetched['actual_send_days'] == 2
        assert fetched['progress_pct'] == 25.0  # 12500 / 50000 * 100

    def test_progress_pct_is_capped_at_100(self, warmup):
        """Sending past target shouldn't push the progress bar past 100%."""
        wsvc, engine, cfg = warmup
        domain = 'oversent.example.com'
        plan = make_plan(wsvc, domain=domain, start_date='2026-09-01', target_date='2026-09-20')
        _seed_pulsation_sent(domain, [('2026-09-01', 90000)])
        fetched = wsvc.get_plan(plan['id'])
        assert fetched['progress_pct'] == 100.0

    def test_progress_ignores_sends_outside_the_plan_window(self, warmup):
        """A send from before start_date (e.g. the domain was already in use)
        shouldn't count toward this plan's ramp progress."""
        wsvc, engine, cfg = warmup
        domain = 'preexisting.example.com'
        plan = make_plan(wsvc, domain=domain, start_date='2026-09-10', target_date='2026-09-25')
        _seed_pulsation_sent(domain, [('2026-08-01', 40000), ('2026-09-12', 1000)])
        fetched = wsvc.get_plan(plan['id'])
        assert fetched['actual_sent_total'] == 1000

    def test_list_plans_also_carries_actual_progress(self, warmup):
        wsvc, engine, cfg = warmup
        domain = 'listcheck.example.com'
        plan = make_plan(wsvc, domain=domain, start_date='2026-09-01', target_date='2026-09-20')
        _seed_pulsation_sent(domain, [('2026-09-01', 25000)])
        listed = next(p for p in wsvc.list_plans() if p['id'] == plan['id'])
        assert listed['progress_pct'] == 50.0
