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

    def test_csv_import_handles_excel_utf8_bom(self, warmup, tmp_path):
        """Excel's "CSV UTF-8" export prefixes the file with a BOM, which
        (without utf-8-sig) corrupts the first header cell into '﻿actual_date'
        -- every row then silently fails as "missing actual_date"."""
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        csv_file = tmp_path / 'bom.csv'
        csv_file.write_bytes(
            '﻿actual_date,actual_total\n2026-09-29,500\n'.encode('utf-8')
        )
        result = wsvc.import_actuals_csv(plan['id'], str(csv_file))
        assert result['imported'] == 1
        assert result['errors'] == []

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


# --------------------------------------------------------------------------
# Plan version history and approval workflow
# --------------------------------------------------------------------------

class TestPlanVersioning:
    def test_creating_a_plan_records_an_initial_version(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        versions = wsvc.get_plan_versions(plan['id'])
        assert len(versions) == 1
        assert versions[0]['version_number'] == 1
        assert versions[0]['reason'] == 'created'
        assert versions[0]['isp_mix'] == plan['isp_mix']

    def test_editing_config_snapshots_the_old_version_before_applying_the_change(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        original_isp_mix = dict(plan['isp_mix'])

        new_isp_mix = {'gmail': 30000, 'microsoft': 15000, 'yahoo': 5000, 'apple': 0, 'comcast': 0, 'other': 0}
        wsvc.update_plan_config(plan['id'], {'isp_mix': new_isp_mix})

        versions = wsvc.get_plan_versions(plan['id'])
        assert len(versions) == 2
        # Most recent first -- the snapshot taken just before the edit.
        assert versions[0]['version_number'] == 2
        assert versions[0]['reason'] == 'config_updated'
        assert versions[0]['isp_mix'] == original_isp_mix  # the OLD config, not the new one
        assert versions[1]['version_number'] == 1
        assert versions[1]['reason'] == 'created'

    def test_approving_a_plan_sets_approved_flag_and_snapshots_a_version(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        assert plan['is_approved'] is False

        approved = wsvc.approve_plan(plan['id'])
        assert approved['is_approved'] is True
        assert approved['approved_at'] is not None

        versions = wsvc.get_plan_versions(plan['id'])
        assert versions[0]['reason'] == 'approved'

    def test_editing_an_approved_plan_automatically_unapproves_it(self, warmup):
        """Human approval must be required before changing an approved
        schedule -- an edit silently keeping is_approved=True would defeat
        that; editing must force a fresh, visible re-approval."""
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.approve_plan(plan['id'])

        edited = wsvc.update_plan_config(plan['id'], {'strategy': 'aggressive'})
        assert edited['is_approved'] is False
        assert edited['approved_at'] is None

    def test_list_plans_surfaces_the_most_recent_decision_and_health_score(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)

        # No actuals/events yet -- should be None, not a stale/fabricated value.
        listed = next(p for p in wsvc.list_plans() if p['id'] == plan['id'])
        assert listed['latest_decision'] is None
        assert listed['latest_health_score'] is None

        for actual_date in ('2026-09-29', '2026-09-30'):
            wsvc.record_actual(plan['id'], actual_date, {
                'actual_total': plan['target_volume'], 'delivery_rate': 98.0, 'hard_bounce_rate': 0.1,
                'soft_bounce_rate': 0.2, 'spam_rate': 0.01, 'unsub_rate': 0.05,
            })
            engine.evaluate(plan['id'], actual_date)

        listed = next(p for p in wsvc.list_plans() if p['id'] == plan['id'])
        # Must reflect the LATEST evaluated date (09-30), not the first.
        assert listed['latest_decision'] is not None
        assert listed['latest_health_score'] is not None


# --------------------------------------------------------------------------
# Plan export (CSV / Excel)
# --------------------------------------------------------------------------

class TestPlanExport:
    def test_csv_export_includes_metadata_header_isp_grid_and_detail(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        csv_text = wsvc.export_plan_to_csv(plan['id'])
        assert f'# Warmup Plan: {plan["name"]}' in csv_text
        assert f'# Domain: {plan["domain"]}' in csv_text
        # ISP-wise weekly grid -- matches the Timeline tab's layout (ISP
        # rows, day columns), not a flat per-day table with ISP columns.
        assert 'Week 1' in csv_text
        assert 'Gmail,' in csv_text
        assert 'Daily Totals,' in csv_text
        assert 'Weekly Total' in csv_text
        # Day-by-day detail section (actuals/decisions) still present.
        assert 'Day-by-day detail' in csv_text
        assert 'Day,Date,Day of Week,Planned Total' in csv_text

    def test_csv_isp_grid_daily_totals_match_schedule(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        schedule = wsvc.get_schedule(plan['id'])
        grid = wsvc.get_isp_weekly_grid(plan['id'])
        # Every ISP row for a given day should sum to that day's planned_total.
        for week in grid:
            for i, day in enumerate(week['days']):
                isp_sum = sum(day.get(f'planned_{isp}', 0) for isp in week['isps'])
                assert isp_sum == day['planned_total']
        # Weekly totals match the actual schedule length.
        assert sum(len(w['days']) for w in grid) == len(schedule)

    def test_csv_export_leaves_unrecorded_actuals_blank_not_zero(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        csv_text = wsvc.export_plan_to_csv(plan['id'])
        detail_section = csv_text.split('Day-by-day detail')[1]
        lines = [l for l in detail_section.splitlines() if l]
        header, data_rows = lines[0], lines[1:]
        assert len(data_rows) > 0
        first_row = data_rows[0].split(',')
        actual_total_idx = header.split(',').index('Actual Total')
        # No actuals ever recorded for this plan -- must be blank, not "0".
        assert first_row[actual_total_idx] == ''

    def test_excel_export_has_isp_grid_and_detail_sheets(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        xlsx_bytes = wsvc.export_plan_to_excel(plan['id'])
        assert xlsx_bytes[:2] == b'PK'  # xlsx files are zip archives

        import io
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes))
        assert set(wb.sheetnames) == {'Plan Details', 'ISP Weekly Grid', 'Day-by-Day Detail'}
        detail_ws = wb['Day-by-Day Detail']
        schedule = wsvc.get_schedule(plan['id'])
        assert detail_ws.max_row == len(schedule) + 1  # +1 header row
        grid_ws = wb['ISP Weekly Grid']
        assert grid_ws['A1'].value == 'Week 1'
        assert grid_ws['A2'].value == 'ISP'

    def test_export_raises_for_unknown_plan(self, warmup):
        wsvc, engine, cfg = warmup
        import pytest as _pytest
        with _pytest.raises(ValueError):
            wsvc.export_plan_to_csv(999999)
        with _pytest.raises(ValueError):
            wsvc.export_plan_to_excel(999999)


# --------------------------------------------------------------------------
# Plan deletion
# --------------------------------------------------------------------------

class TestPlanDeletion:
    def test_deleting_a_plan_removes_it_and_all_scoped_data(self, warmup):
        wsvc, engine, cfg = warmup
        plan = make_plan(wsvc)
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 500})
        engine.evaluate(plan['id'], '2026-09-29')

        wsvc.delete_plan(plan['id'])

        assert wsvc.get_plan(plan['id']) is None
        assert wsvc.get_schedule(plan['id']) == []
        assert wsvc.get_actuals(plan['id']) == []
        assert wsvc.get_events(plan['id']) == []
        assert wsvc.get_plan_versions(plan['id']) == []

    def test_deleting_one_plan_does_not_touch_others(self, warmup):
        wsvc, engine, cfg = warmup
        plan_a = make_plan(wsvc, domain='keep-me.example.com')
        plan_b = make_plan(wsvc, domain='delete-me.example.com')
        wsvc.delete_plan(plan_b['id'])
        assert wsvc.get_plan(plan_a['id']) is not None
        assert wsvc.get_plan(plan_b['id']) is None

    def test_deleting_unknown_plan_raises(self, warmup):
        wsvc, engine, cfg = warmup
        import pytest as _pytest
        with _pytest.raises(ValueError):
            wsvc.delete_plan(999999)


# --------------------------------------------------------------------------
# Importing a client-supplied schedule (not algorithmically generated)
# --------------------------------------------------------------------------

def _write_schedule_csv(path, rows):
    with open(path, 'w', newline='') as f:
        f.write('date,gmail,microsoft,yahoo,apple,comcast,other\n')
        for r in rows:
            f.write(','.join(str(v) for v in r) + '\n')


class TestCustomScheduleImport:
    def test_parses_a_well_formed_schedule_csv(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'schedule.csv'
        _write_schedule_csv(csv_path, [
            ['2026-11-01', 500, 300, 100, 50, 0, 100],
            ['2026-11-02', 1000, 600, 200, 100, 0, 200],
        ])
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert errors == []
        assert len(rows) == 2
        assert rows[0]['date'] == '2026-11-01'
        assert rows[0]['gmail'] == 500.0

    def test_handles_excel_utf8_bom_in_schedule_csv(self, warmup, tmp_path):
        """The exact bug report: an Excel "CSV UTF-8" export prefixes a BOM,
        corrupting the 'date' header so every row failed as "missing date"
        and the whole import silently produced zero rows."""
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'bom_schedule.csv'
        csv_path.write_bytes(
            '﻿date,gmail,microsoft,yahoo,apple,comcast,other\n'
            '2026-11-01,500,300,100,50,0,100\n'
            '2026-11-02,1000,600,200,100,0,200\n'.encode('utf-8')
        )
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert errors == []
        assert len(rows) == 2

    def test_handles_semicolon_delimiter_and_aliased_isp_headers(self, warmup, tmp_path):
        """Real client files aren't always comma-delimited with exact 'gmail'
        headers -- Outlook/AOL/iCloud/Xfinity are common real-world labels."""
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'semicolon.csv'
        csv_path.write_text(
            'Send Date;Gmail Volume;Outlook;AOL;iCloud;Xfinity;Misc\n'
            '12/01/2026;500;300;100;50;0;100\n'
            '12/02/2026;750;400;150;75;0;150\n'
        )
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert errors == []
        assert len(rows) == 2
        assert rows[0]['date'] == '2026-12-01'
        assert rows[0]['microsoft'] == 300.0  # from 'Outlook'
        assert rows[0]['yahoo'] == 100.0      # from 'AOL'
        assert rows[0]['apple'] == 50.0       # from 'iCloud'
        assert rows[0]['comcast'] == 0.0      # from 'Xfinity'
        assert rows[0]['other'] == 100.0      # from 'Misc'

    def test_handles_tab_delimiter_and_human_date_format(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'tabbed.csv'
        csv_path.write_text('Day\tGmail\tMicrosoft\tYahoo\n1-Dec-2026\t500\t300\t100\n2-Dec-2026\t750\t400\t150\n')
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert errors == []
        assert len(rows) == 2
        assert rows[0]['date'] == '2026-12-01'

    def test_handles_excel_serial_date_numbers(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'serial.csv'
        # 46357 == 2026-12-01 as an Excel serial date (days since 1899-12-30)
        csv_path.write_text('Schedule,Gmail,Microsoft\n46357,500,300\n46358,750,400\n')
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert errors == []
        assert len(rows) == 2
        assert rows[0]['date'] == '2026-12-01'

    def test_never_misreads_a_plain_volume_column_as_dates(self, warmup, tmp_path):
        """Regression: dateutil.parser.parse('500') "succeeds" by guessing
        it's the bare year 500 AD -- without a guard, a transposed file (ISP
        rows, week columns of plain numbers) would silently produce garbage
        dates instead of a clear error."""
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'transposed.csv'
        csv_path.write_text('ISP,Week1,Week2\nGmail,500,750\nMicrosoft,300,400\n')
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert rows == []
        assert any('Could not find a date column' in e and 'ISP, Week1, Week2' in e for e in errors)

    def test_sorts_out_of_order_rows_chronologically(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'schedule.csv'
        _write_schedule_csv(csv_path, [
            ['2026-11-02', 1000, 600, 200, 100, 0, 200],
            ['2026-11-01', 500, 300, 100, 50, 0, 100],
        ])
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert [r['date'] for r in rows] == ['2026-11-01', '2026-11-02']

    def test_bad_rows_are_skipped_with_errors_not_fatal(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'schedule.csv'
        _write_schedule_csv(csv_path, [
            ['2026-11-01', 500, 300, 100, 50, 0, 100],
            ['not-a-date', 1000, 600, 200, 100, 0, 200],
            ['2026-11-03', 'oops', 600, 200, 100, 0, 200],
        ])
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert len(rows) == 1
        assert len(errors) == 2

    def test_missing_isp_columns_default_to_zero(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'schedule.csv'
        with open(csv_path, 'w') as f:
            f.write('date,gmail,microsoft\n2026-11-01,500,300\n2026-11-02,1000,600\n')
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert errors == []
        assert rows[0]['yahoo'] == 0
        assert rows[0]['comcast'] == 0

    def test_create_plan_from_schedule_derives_dates_and_target_from_rows(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'schedule.csv'
        _write_schedule_csv(csv_path, [
            ['2026-11-01', 500, 300, 100, 50, 0, 100],
            ['2026-11-02', 750, 400, 150, 75, 0, 150],
            ['2026-11-05', 2000, 1200, 400, 200, 0, 400],
        ])
        rows, _ = wsvc.parse_custom_schedule_csv(str(csv_path))
        plan = wsvc.create_plan_from_schedule({
            'name': 'Imported Plan', 'domain': 'imported.example.com', 'warming_type': 'new_domain',
        }, rows)
        assert plan['start_date'] == '2026-11-01'
        assert plan['target_date'] == '2026-11-05'
        assert plan['target_volume'] == 2000 + 1200 + 400 + 200 + 0 + 400
        assert plan['isp_mix']['gmail'] == 2000

    def test_imported_plan_schedule_matches_uploaded_rows_exactly(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'schedule.csv'
        _write_schedule_csv(csv_path, [
            ['2026-11-01', 500, 300, 100, 50, 0, 100],
            ['2026-11-02', 1000, 600, 200, 100, 0, 200],
        ])
        rows, _ = wsvc.parse_custom_schedule_csv(str(csv_path))
        plan = wsvc.create_plan_from_schedule({
            'name': 'Imported Plan', 'domain': 'imported2.example.com', 'warming_type': 'new_domain',
        }, rows)
        schedule = wsvc.get_schedule(plan['id'])
        assert len(schedule) == 2
        assert schedule[0]['planned_gmail'] == 500
        assert schedule[0]['planned_total'] == 500 + 300 + 100 + 50 + 0 + 100
        assert schedule[1]['planned_gmail'] == 1000

    def test_imported_plan_tracks_normally_through_the_decision_engine(self, warmup, tmp_path):
        """The whole point: once imported, actuals/decisions/health score
        work exactly as they do for a generated plan -- no special-casing."""
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'schedule.csv'
        _write_schedule_csv(csv_path, [
            ['2026-09-29', 225, 125, 75, 25, 0, 50],
            ['2026-09-30', 266, 148, 89, 30, 0, 58],
        ])
        rows, _ = wsvc.parse_custom_schedule_csv(str(csv_path))
        plan = wsvc.create_plan_from_schedule({
            'name': 'Tracked Import', 'domain': REAL_DOMAIN, 'warming_type': 'new_domain',
        }, rows)
        wsvc.record_actual(plan['id'], '2026-09-29', {
            'actual_total': 500, 'delivery_rate': 98.0, 'hard_bounce_rate': 0.1,
            'soft_bounce_rate': 0.2, 'spam_rate': 0.01, 'unsub_rate': 0.05,
        })
        result = engine.evaluate(plan['id'], '2026-09-29')
        assert result['decision'] in ('increase', 'hold', 'reduce', 'pause')
        assert result['health_score'] is not None

    def test_create_plan_from_schedule_requires_at_least_two_rows(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        import pytest as _pytest
        with _pytest.raises(ValueError):
            wsvc.create_plan_from_schedule({
                'name': 'Too Short', 'domain': 'short.example.com', 'warming_type': 'new_domain',
            }, [{'date': '2026-11-01', 'gmail': 500, 'microsoft': 0, 'yahoo': 0, 'apple': 0, 'comcast': 0, 'other': 0}])


# --------------------------------------------------------------------------
# Transposed weekly grid (ISP rows, "Day N" columns, stacked "Week N"
# blocks) -- the real shape an agency warm-up plan is actually built in,
# with noise columns/rows from the surrounding spreadsheet ignored.
# --------------------------------------------------------------------------

_TRANSPOSED_GRID_FIXTURE = (
    ',,,,,,,,,,,,,,,,\n'
    'Weekly Warm Up Breakdown,,,,,,,,,,,,,,,Mailable Base,\n'
    ',,,,,,,,,,,"FOR INTERNAL CALCULATIONS ONLY\nCOPY FILE AND REMOVE","",,,Gmail,"36,860"\n'
    '~15 Day Engaged,,,,,,,,,,,,,,,Yahoo,"8,177"\n'
    'Week One,Day 1,Day 2,Day 3,Day 4,Day 5,Day 6,Day 7,Weekly Totals,,,Audi Denver,Current Total,Needed,Remaining,Perdue,"2,983"\n'
    'Gmail,50,75,125,200,300,450,650,"1,850",,,"9,485","245,110","7,487","-237,623",Comcast,"1,445"\n'
    'Perdue,50,75,100,125,150,200,300,"1,000",,,"1,380","31,583","1,581","-30,002",Other,"5,657"\n'
    'Yahoo,50,75,100,125,150,200,300,"1,000",,,"1,857","68,877","1,326","-67,551",Total,"55,122"\n'
    'Comcast,50,75,100,125,150,200,300,"1,000",,,,,,,,\n'
    'Other,100,200,300,400,600,800,"1,250","3,650",,,"2,569","121,057","2,869","-118,188",,\n'
    'Daily Totals,300,500,725,975,"1,350","1,850","2,800","8,500",,,"15,291",,,,,\n'
    ',,,,,,,,,,,Total Volume,13263,,,,\n'
    '~30 Day Engaged,,,,,,,,,,,,,,,,\n'
    'Week Two,Day 8,Day 9,Day 10,Day 11,Day 12,Day 13,Day 14,Weekly Totals,,,,,,,,\n'
    'Gmail,900,"1,250","1,750","2,500","3,500","5,000","7,000","21,900",,,,,,,,\n'
    'Perdue,400,500,650,800,"1,000","1,250","1,500","6,100",,,,,,,,\n'
    'Yahoo,400,600,900,"1,200","1,600","2,000","2,500","9,200",,,,,,,,\n'
    'Comcast,400,600,900,"1,200","1,445","1,700","1,900","8,145",,,,,,,,\n'
    'Other,"1,750","2,500","3,500","5,657","7,000","9,000","11,000","40,407",,,,,,,,\n'
    'Daily Totals,"3,850","5,450","7,700","11,357","14,545","18,950","23,900","85,752",,,,,,,,\n'
    ',,,,,,,,,,,,,,,,\n'
    '~60 Day Engaged,,,,,,,,,,,,,,,,\n'
    'Week Three,Day 15,,,,,,,Weekly Totals,,,,,,,,\n'
    'Gmail,"9,500",,,,,,,"9,500",,,,,,,,\n'
    'Perdue,"2,000",,,,,,,"2,000",,,,,,,,\n'
    'Yahoo,"3,000",,,,,,,"3,000",,,,,,,,\n'
    'Comcast,"2,200",,,,,,,"2,200",,,,,,,,\n'
    'Other,"11,000",,,,,,,"11,000",,,,,,,,\n'
    'Daily Totals,"27,700",0,0,0,0,0,0,"27,700",,,,,,,,\n'
    ',,,,,,,,,,,,,,,,\n'
    '90 Day Engaged,,,,,,,,,,,,,,,,\n'
    'Week Four,Monday,Tuesday,Wednesday,Thursday,Friday,Saturday,Sunday,Weekly Totals,,,,,,,,\n'
    'Gmail,,,,,,,,0,,,,,,,,\n'
    'Microsoft,,,,,,,,0,,,,,,,,\n'
    'Daily Totals,0,0,0,0,0,0,0,0,,,,,,,,\n'
)


class TestTransposedWeeklyGridImport:
    def test_parses_real_world_agency_file_shape(self, warmup, tmp_path):
        """Modeled directly on an actual client file -- stacked Week blocks,
        a 'FOR INTERNAL CALCULATIONS ONLY' notes cell, a 'Mailable Base'
        side-table, an 'Audi Denver' current/needed/remaining side-table, an
        unrecognized 'Perdue' ISP row, and a trailing all-zero weekday-named
        block that must NOT be picked up (no 'Day N' pattern -> ignored)."""
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'agency_plan.csv'
        csv_path.write_text(_TRANSPOSED_GRID_FIXTURE)
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path), start_date='2026-12-01')
        assert len(rows) == 15  # Week One (7) + Week Two (7) + Week Three (1) -- the Monday-block is ignored
        assert rows[0]['date'] == '2026-12-01'
        assert rows[-1]['date'] == '2026-12-15'
        # Day 1: Gmail 50, Perdue 50 (-> other), Yahoo 50, Comcast 50, Other 100+50=150
        assert rows[0]['gmail'] == 50
        assert rows[0]['yahoo'] == 50
        assert rows[0]['comcast'] == 50
        assert rows[0]['other'] == 150
        assert rows[0]['microsoft'] == 0
        assert any('Perdue' in e and 'Other' in e for e in errors)

    def test_transposed_grid_day_totals_match_source_daily_totals_row(self, warmup, tmp_path):
        """Cross-check against the file's own 'Daily Totals' row (which this
        parser doesn't read, it's recomputed) -- confirms no value is lost
        or double-counted across the noise columns."""
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'agency_plan.csv'
        csv_path.write_text(_TRANSPOSED_GRID_FIXTURE)
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path), start_date='2026-12-01')
        day1 = rows[0]
        assert sum(day1[isp] for isp in cfg.ISP_BUCKETS) == 300  # file's Daily Totals for Day 1
        day8 = rows[7]
        assert sum(day8[isp] for isp in cfg.ISP_BUCKETS) == 3850  # file's Daily Totals for Day 8

    def test_transposed_grid_requires_start_date(self, warmup, tmp_path):
        """No real calendar dates exist in this shape -- without a start
        date there's nothing to anchor day numbers to, so it must fail
        clearly rather than guess."""
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'agency_plan.csv'
        csv_path.write_text(_TRANSPOSED_GRID_FIXTURE)
        rows, errors = wsvc.parse_custom_schedule_csv(str(csv_path))
        assert rows == []
        assert any('weekly grid' in e.lower() for e in errors)

    def test_imported_transposed_plan_tracks_normally(self, warmup, tmp_path):
        wsvc, engine, cfg = warmup
        csv_path = tmp_path / 'agency_plan.csv'
        csv_path.write_text(_TRANSPOSED_GRID_FIXTURE)
        rows, _ = wsvc.parse_custom_schedule_csv(str(csv_path), start_date='2026-09-29')
        plan = wsvc.create_plan_from_schedule({
            'name': 'Agency Import', 'domain': REAL_DOMAIN, 'warming_type': 'new_ip_and_domain',
        }, rows)
        assert plan['start_date'] == '2026-09-29'
        wsvc.record_actual(plan['id'], '2026-09-29', {'actual_total': 300, 'delivery_rate': 98.0})
        result = engine.evaluate(plan['id'], '2026-09-29')
        assert result['decision'] in ('increase', 'hold', 'reduce', 'pause')
