"""
Deliverability Warm-up Planner -- decision engine.

Pure evaluation logic: given a plan, its schedule, and its actuals/signals,
compute a health score and an Increase/Hold/Reduce/Pause recommendation with
explicit, structured reasons. Kept separate from warmup_service.py (which
owns storage/CRUD) so this can be tested as a pure function of its inputs.

DATA NOTE on "reputation": Google Postmaster Tools v2 (see gpt_service.py's
MIGRATION NOTES) dropped domain/IP reputation entirely -- the `reputation`
column in gpt_data is frozen/NULL for any date after the v1->v2 cutover, so
it is NEVER read here. Gmail's signal instead comes from its still-live
spam_rate (a real, current number). Microsoft's signal comes from SNDS's
filter_result/spam_rate/trap_hits, which are unaffected by that migration
and remain a genuine reputation-adjacent signal. Yahoo/Apple/Comcast/Other
get no reputation-category signal at all -- nothing publishes one, for any
sender, anywhere. This engine never fabricates one for them.
"""
from datetime import date, timedelta
from typing import Dict, List, Optional

import warmup_service as wsvc
import warmup_thresholds as cfg

DECISION_PAUSE = 'pause'
DECISION_REDUCE = 'reduce'
DECISION_HOLD = 'hold'
DECISION_INCREASE = 'increase'

SEVERITY_CRITICAL = 'critical'
SEVERITY_WARNING = 'warning'
SEVERITY_OK = 'ok'


def _reason(signal: str, severity: str, message: str, value=None, threshold=None) -> Dict:
    return {
        'signal': signal, 'severity': severity, 'message': message,
        'value': value, 'threshold': threshold,
    }


def _rate_check(reasons: List[Dict], signal: str, value: Optional[float],
                 warning: float, critical: float, higher_is_worse: bool = True,
                 label: str = None) -> str:
    """Evaluate one rate signal against warning/critical thresholds.

    Returns the severity ('ok'/'warning'/'critical') so callers can fold it
    into sustained-breach tracking. Appends a reason only for warning/critical
    -- healthy signals aren't noise in the explanation, they show up as the
    positive side of the score instead (see build_positive_signals).
    """
    if value is None:
        return SEVERITY_OK
    label = label or signal
    breached_critical = (value >= critical) if higher_is_worse else (value <= critical)
    breached_warning = (value >= warning) if higher_is_worse else (value <= warning)
    if breached_critical:
        reasons.append(_reason(signal, SEVERITY_CRITICAL,
                                f'{label} is {value}%, at or above the critical threshold ({critical}%).',
                                value, critical))
        return SEVERITY_CRITICAL
    if breached_warning:
        reasons.append(_reason(signal, SEVERITY_WARNING,
                                f'{label} is {value}%, at or above the warning threshold ({warning}%).',
                                value, warning))
        return SEVERITY_WARNING
    return SEVERITY_OK


def _check_authentication(domain: str) -> Optional[Dict]:
    """Best-effort SPF/DMARC presence check -- a genuinely missing record
    (not a weak one) is treated as a hard infrastructure fault, distinct
    from a deliverability/reputation problem, and forces PAUSE regardless
    of other signals (principle: distinguish infrastructure/auth issues
    from reputation risk -- fixing DNS, not slowing the ramp, is the fix).
    """
    try:
        from deliverability_agent.deliverability.dns_checker import DNSChecker
        spf = DNSChecker.get_spf(domain)
        dmarc = DNSChecker.get_dmarc(domain)
        if not spf:
            return _reason('spf_missing', SEVERITY_CRITICAL, f'{domain} has no SPF record.', None, None)
        if not dmarc:
            return _reason('dmarc_missing', SEVERITY_CRITICAL, f'{domain} has no DMARC record.', None, None)
    except Exception as e:
        print(f'Warmup: authentication check unavailable for {domain}: {e}')
    return None


def _check_spamhaus(domain: str) -> Optional[Dict]:
    """Best-effort Spamhaus check -- never lets a lookup failure break the engine."""
    try:
        from spamhaus_service import get_spamhaus_status_map
        status_map = get_spamhaus_status_map([domain])
        status = status_map.get(domain.strip().lower())
        if status == 'listed':
            return _reason('spamhaus', SEVERITY_CRITICAL,
                            f'{domain} is currently listed on Spamhaus DBL.', 'listed', None)
    except Exception as e:
        print(f'Warmup: Spamhaus check unavailable for {domain}: {e}')
    return None


def _gmail_signal(domain: str, on_date: str) -> Optional[Dict]:
    """Best-effort Gmail signal from GPT's still-live spam_rate.

    Never reads gpt_data.reputation -- see module docstring. Returns None
    (not a fabricated 'ok') if no GPT data exists for this domain/date.
    """
    try:
        import sqlite3
        from gpt_service import GPT_DB_PATH
        conn = sqlite3.connect(GPT_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT spam_rate FROM gpt_data WHERE domain = ? AND data_date = ?',
            (domain, on_date)
        )
        row = cursor.fetchone()
        conn.close()
        if row and row[0] is not None:
            return {'spam_rate': row[0]}
    except Exception as e:
        print(f'Warmup: GPT signal unavailable for {domain}: {e}')
    return None


def _microsoft_signal(ip_address: Optional[str], on_date: str) -> Optional[Dict]:
    """Best-effort Microsoft signal from SNDS. Only meaningful when a plan
    has a concrete IP (migration/new-IP plans) -- pure domain warmups have
    no IP to look up, and this returns None rather than guessing one.
    """
    if not ip_address:
        return None
    try:
        import sqlite3
        from data_paths import data_path
        conn = sqlite3.connect(data_path('snds_data.db'))
        cursor = conn.cursor()
        cursor.execute(
            'SELECT spam_rate, complaint_rate, trap_hits, filter_result '
            'FROM snds_data WHERE ip_address = ? AND data_date = ?',
            (ip_address, on_date)
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'spam_rate': row[0], 'complaint_rate': row[1], 'trap_hits': row[2], 'filter_result': row[3]}
    except Exception as e:
        print(f'Warmup: SNDS signal unavailable for {ip_address}: {e}')
    return None


def _sustained_critical_signals(plan_id: int, signal_names: set, today: str) -> set:
    """Which of today's critical signals were ALSO critical on the
    immediately preceding evaluated day -- used to decide REDUCE vs PAUSE
    (principle: one bad day shouldn't pause the whole warm-up, but the same
    problem two days running is a different story).
    """
    events = wsvc.get_events(plan_id)
    prior = [e for e in events if e['event_date'] < today]
    if not prior:
        return set()
    last = prior[-1]
    prior_critical = {r['signal'] for r in last['reasons'] if r['severity'] == SEVERITY_CRITICAL}
    return signal_names & prior_critical


def evaluate(plan_id: int, on_date: Optional[str] = None) -> Dict:
    """Evaluate a plan's status for `on_date` (default: today) and persist
    the resulting decision as a warmup_event. Returns the full result dict.
    """
    plan = wsvc.get_plan(plan_id)
    if not plan:
        raise ValueError(f'No warmup plan with id {plan_id}')

    on_date = on_date or date.today().isoformat()
    schedule = wsvc.get_schedule(plan_id)
    today_planned = next((d for d in schedule if d['scheduled_date'] == on_date), None)
    actual = wsvc.get_actual(plan_id, on_date)

    reasons: List[Dict] = []
    positives: List[str] = []

    if not today_planned:
        return _no_data_result(plan_id, on_date, 'This date is outside the plan schedule.')
    if not actual or actual.get('actual_total') is None:
        return _no_data_result(plan_id, on_date, "No actuals recorded yet for today.")

    # --- Spamhaus + authentication: automatic, overriding PAUSE signals ---
    spamhaus_reason = _check_spamhaus(plan['domain'])
    if spamhaus_reason:
        reasons.append(spamhaus_reason)
    auth_reason = _check_authentication(plan['domain'])
    if auth_reason:
        reasons.append(auth_reason)
    force_pause = bool(spamhaus_reason or auth_reason)

    # --- Volume adherence --------------------------------------------------
    planned_total = today_planned['planned_total']
    actual_total = actual['actual_total']
    variance_pct = round((actual_total - planned_total) / planned_total * 100, 1) if planned_total else 0
    volume_severity = SEVERITY_OK
    if variance_pct >= cfg.VOLUME_OVERSHOOT_RISK_PCT:
        volume_severity = SEVERITY_CRITICAL
        reasons.append(_reason(
            'volume_overshoot', SEVERITY_CRITICAL,
            f'Actual volume ({actual_total:,}) is {variance_pct}% above planned ({planned_total:,}) -- ramping faster than the plan.',
            variance_pct, cfg.VOLUME_OVERSHOOT_RISK_PCT))
    elif abs(variance_pct) >= cfg.VOLUME_VARIANCE_WARNING_PCT:
        volume_severity = SEVERITY_WARNING
        direction = 'above' if variance_pct > 0 else 'below'
        reasons.append(_reason(
            'volume_variance', SEVERITY_WARNING,
            f'Actual volume is {abs(variance_pct)}% {direction} planned.',
            variance_pct, cfg.VOLUME_VARIANCE_WARNING_PCT))
    else:
        positives.append('Volume on plan')

    # --- Delivery / bounce / spam / unsub ----------------------------------
    delivery_severity = _rate_check(
        reasons, 'delivery_rate', actual.get('delivery_rate'),
        cfg.DELIVERY_RATE_WARNING_PCT, cfg.DELIVERY_RATE_CRITICAL_PCT,
        higher_is_worse=False, label='Delivery rate')
    if delivery_severity == SEVERITY_OK and actual.get('delivery_rate') is not None:
        positives.append('Delivery rate healthy')

    hard_bounce_sev = _rate_check(
        reasons, 'hard_bounce_rate', actual.get('hard_bounce_rate'),
        cfg.HARD_BOUNCE_WARNING_PCT, cfg.HARD_BOUNCE_CRITICAL_PCT, label='Hard bounce rate')
    soft_bounce_sev = _rate_check(
        reasons, 'soft_bounce_rate', actual.get('soft_bounce_rate'),
        cfg.SOFT_BOUNCE_WARNING_PCT, cfg.SOFT_BOUNCE_CRITICAL_PCT, label='Soft bounce rate')
    bounce_severity = max(hard_bounce_sev, soft_bounce_sev, key=lambda s: [SEVERITY_OK, SEVERITY_WARNING, SEVERITY_CRITICAL].index(s))
    if bounce_severity == SEVERITY_OK and (actual.get('hard_bounce_rate') is not None or actual.get('soft_bounce_rate') is not None):
        positives.append('Bounce rates stable')

    spam_severity = _rate_check(
        reasons, 'spam_rate', actual.get('spam_rate'),
        cfg.SPAM_WARNING_PCT, cfg.SPAM_CRITICAL_PCT, label='Spam complaint rate')
    if spam_severity == SEVERITY_OK and actual.get('spam_rate') is not None:
        positives.append('Low complaint rate')

    _rate_check(reasons, 'unsub_rate', actual.get('unsub_rate'),
                cfg.UNSUB_WARNING_PCT, cfg.UNSUB_CRITICAL_PCT, label='Unsubscribe rate')

    # --- Reputation-backed ISPs (Gmail via GPT spam_rate, Microsoft via SNDS) ---
    gmail_signal = _gmail_signal(plan['domain'], on_date)
    reputation_severity = SEVERITY_OK
    if gmail_signal and gmail_signal.get('spam_rate') is not None:
        sev = _rate_check(reasons, 'gmail_spam_rate', gmail_signal['spam_rate'],
                           cfg.SPAM_WARNING_PCT, cfg.SPAM_CRITICAL_PCT, label='Gmail spam rate (Postmaster)')
        if sev == SEVERITY_OK:
            positives.append('Gmail reputation healthy')
        reputation_severity = sev

    ms_ip = plan.get('new_ip') or plan.get('old_ip')
    ms_signal = _microsoft_signal(ms_ip, on_date)
    if ms_signal:
        if ms_signal.get('trap_hits'):
            reasons.append(_reason('microsoft_trap_hits', SEVERITY_CRITICAL,
                                    f"Microsoft SNDS reported {ms_signal['trap_hits']} spam trap hit(s).",
                                    ms_signal['trap_hits'], 0))
            reputation_severity = SEVERITY_CRITICAL
        elif ms_signal.get('filter_result') in ('Block', 'Bulk'):
            sev = SEVERITY_CRITICAL if ms_signal['filter_result'] == 'Block' else SEVERITY_WARNING
            reasons.append(_reason('microsoft_filter_result', sev,
                                    f"Microsoft SNDS filter result is '{ms_signal['filter_result']}'.",
                                    ms_signal['filter_result'], None))
            reputation_severity = max(reputation_severity, sev, key=lambda s: [SEVERITY_OK, SEVERITY_WARNING, SEVERITY_CRITICAL].index(s))
        else:
            positives.append('Microsoft SNDS signal healthy')

    # --- Health score (weighted, 0-100) ------------------------------------
    sub_scores = {
        'delivery': 100 if delivery_severity == SEVERITY_OK else (60 if delivery_severity == SEVERITY_WARNING else 20),
        'bounce': 100 if bounce_severity == SEVERITY_OK else (60 if bounce_severity == SEVERITY_WARNING else 20),
        'spam': 100 if spam_severity == SEVERITY_OK else (60 if spam_severity == SEVERITY_WARNING else 10),
        'reputation': 100 if reputation_severity == SEVERITY_OK else (60 if reputation_severity == SEVERITY_WARNING else 20),
        'volume_adherence': 100 if volume_severity == SEVERITY_OK else (70 if volume_severity == SEVERITY_WARNING else 40),
    }
    health_score = round(sum(sub_scores[k] * cfg.SCORE_WEIGHTS[k] for k in sub_scores) / 100)

    if health_score >= cfg.SCORE_HEALTHY_MIN:
        health_band = 'Healthy'
    elif health_score >= cfg.SCORE_WATCH_MIN:
        health_band = 'Watch'
    elif health_score >= cfg.SCORE_RISK_MIN:
        health_band = 'Risk'
    else:
        health_band = 'Critical'

    # --- Decision -----------------------------------------------------------
    critical_signals = {r['signal'] for r in reasons if r['severity'] == SEVERITY_CRITICAL}
    warning_signals = {r['signal'] for r in reasons if r['severity'] == SEVERITY_WARNING}

    if force_pause:
        decision = DECISION_PAUSE
        explanation = _explain(plan, DECISION_PAUSE, reasons, positives)
    elif critical_signals:
        sustained = _sustained_critical_signals(plan_id, critical_signals, on_date)
        if sustained and len(sustained) >= 1 and len(critical_signals & sustained) == len(critical_signals):
            # Every critical signal today was also critical yesterday -- sustained, not transient.
            decision = DECISION_PAUSE
        else:
            decision = DECISION_REDUCE
        explanation = _explain(plan, decision, reasons, positives)
    elif warning_signals:
        decision = DECISION_HOLD
        explanation = _explain(plan, decision, reasons, positives)
    else:
        decision = DECISION_INCREASE
        explanation = _explain(plan, decision, reasons, positives)

    result = {
        'plan_id': plan_id, 'date': on_date, 'decision': decision,
        'health_score': health_score, 'health_band': health_band,
        'sub_scores': sub_scores, 'reasons': reasons, 'positives': positives,
        'explanation': explanation, 'planned_total': planned_total, 'actual_total': actual_total,
        'variance_pct': variance_pct, 'thresholds_version': cfg.THRESHOLDS_VERSION,
    }
    wsvc.record_event(plan_id, on_date, decision, health_score, health_band, reasons)
    return result


def recalculate_all(plan_id: int) -> List[Dict]:
    """Evaluate every date that has actuals recorded, in chronological order.

    Needed because entering/importing a date other than "today" (e.g.
    backfilling, or a CSV of historical days) doesn't otherwise get a
    decision computed at all -- the UI's normal flow only re-evaluates
    today's date. Chronological order matters: sustained-critical-day
    detection (see _sustained_critical_signals) depends on the prior day's
    event already being recorded.
    """
    actuals = wsvc.get_actuals(plan_id)
    results = []
    for actual in sorted(actuals, key=lambda a: a['actual_date']):
        results.append(evaluate(plan_id, actual['actual_date']))
    return results


def _no_data_result(plan_id: int, on_date: str, message: str) -> Dict:
    return {
        'plan_id': plan_id, 'date': on_date, 'decision': None, 'health_score': None,
        'health_band': None, 'sub_scores': {}, 'reasons': [], 'positives': [],
        'explanation': message, 'planned_total': None, 'actual_total': None,
        'variance_pct': None, 'thresholds_version': cfg.THRESHOLDS_VERSION,
    }


_DECISION_VERBS = {
    DECISION_INCREASE: 'Increase tomorrow\'s volume on schedule.',
    DECISION_HOLD: 'Hold today\'s volume and continue monitoring.',
    DECISION_REDUCE: 'Reduce volume for the affected ISP(s) and re-evaluate tomorrow.',
    DECISION_PAUSE: 'Pause the ramp until this is resolved.',
}


def _explain(plan: Dict, decision: str, reasons: List[Dict], positives: List[str]) -> str:
    critical = [r for r in reasons if r['severity'] == SEVERITY_CRITICAL]
    warning = [r for r in reasons if r['severity'] == SEVERITY_WARNING]

    if decision == DECISION_INCREASE:
        body = 'Current volume is performing within expected thresholds'
        if positives:
            body += ' (' + ', '.join(positives[:3]) + ')'
        body += '. No warning or critical signals detected.'
    elif decision == DECISION_HOLD:
        signal_text = '; '.join(r['message'] for r in warning) or 'a signal is near a warning threshold'
        body = f'The ramp should be held for today because {signal_text}'
    elif decision == DECISION_REDUCE:
        signal_text = '; '.join(r['message'] for r in critical) or 'a signal breached a critical threshold'
        body = f'{signal_text} This is treated as a single-day issue, not sustained -- volume is reduced rather than paused.'
    else:  # PAUSE
        signal_text = '; '.join(r['message'] for r in critical) or 'a critical, overriding signal was detected'
        body = f'{signal_text} This is severe enough (or has persisted across consecutive days) to pause the ramp entirely.'

    return f'Recommendation: {decision.upper()}\n\n{body}\n\nRecommended action: {_DECISION_VERBS[decision]}'
