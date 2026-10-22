"""
Centralized, versioned configuration for the Deliverability Warm-up Planner.

Every threshold the decision engine (warmup_decision_engine.py) uses lives
here, not scattered inline across files the way pulsation_service.py and
health_score_service.py do it today. To change warm-up behavior, change a
number here -- never hardcode a new cutoff inside the decision engine itself.

Defaults below are sensible starting points, not universal ISP rules -- they
are deliberately conservative and meant to be tuned by the deliverability
team over time.
"""

THRESHOLDS_VERSION = "1.0.0"

# --- Volume adherence -------------------------------------------------
# How far actual daily volume can drift from the planned target before
# it's flagged. Overshooting is treated as more dangerous than undershooting
# (ramping faster than planned is the real reputation risk).
VOLUME_VARIANCE_WARNING_PCT = 20.0
VOLUME_OVERSHOOT_RISK_PCT = 35.0

# --- Bounce rates (%) ---------------------------------------------------
HARD_BOUNCE_WARNING_PCT = 2.0
HARD_BOUNCE_CRITICAL_PCT = 5.0
SOFT_BOUNCE_WARNING_PCT = 1.5
SOFT_BOUNCE_CRITICAL_PCT = 3.0

# --- Delivery rate (%) --------------------------------------------------
# Below these is a problem; above is fine (no upper bound).
DELIVERY_RATE_WARNING_PCT = 95.0
DELIVERY_RATE_CRITICAL_PCT = 90.0

# --- Spam complaint rate (%) --------------------------------------------
# Matches the agent's existing check_gmail_reputation danger line (0.3%
# is the industry figure Gmail itself throttles around) -- see
# deliverability_agent/deliverability/analyzer.py.
SPAM_WARNING_PCT = 0.1
SPAM_CRITICAL_PCT = 0.3

# --- Unsubscribe rate (%) -----------------------------------------------
UNSUB_WARNING_PCT = 0.5
UNSUB_CRITICAL_PCT = 1.0

# --- Health score weights (must sum to 100) -----------------------------
SCORE_WEIGHTS = {
    "delivery": 25,
    "bounce": 25,
    "spam": 25,
    "reputation": 15,
    "volume_adherence": 10,
}

# --- Health score bands ---------------------------------------------------
SCORE_HEALTHY_MIN = 90
SCORE_WATCH_MIN = 70
SCORE_RISK_MIN = 40
# below SCORE_RISK_MIN => Critical

# --- ISP reputation data availability ------------------------------------
# ISPs with a REAL reputation signal available today. Nothing outside this
# set ever gets a reputation score displayed -- there is no public API for
# Yahoo/Apple/Comcast reputation, industry-wide, not just in this codebase.
# Those ISPs still contribute volume/delivery/bounce/open/click signals,
# just never a reputation number.
REPUTATION_BACKED_ISPS = {"gmail", "microsoft"}

ISP_BUCKETS = ["gmail", "microsoft", "yahoo", "apple", "comcast", "other"]

# --- Nice-number rounding for the generated ramp --------------------------
# Each day's planned volume (per ISP) is rounded to one of these steps based
# on its own magnitude, e.g. 612 -> 600, not left as a precise-but-arbitrary
# curve value -- matches how these plans are actually built and read day to
# day. List of (upper_bound_exclusive, step); first match wins.
NICE_ROUNDING_STEPS = [
    (100, 10),
    (500, 25),
    (2000, 50),
    (10000, 100),
    (50000, 500),
    (float('inf'), 1000),
]

# --- Curve strategy -> maximum daily growth rate -------------------------
# 1.00 = volume may double day over day (Aggressive); lower is slower/safer.
STRATEGY_MAX_DAILY_GROWTH = {
    "conservative": 0.30,
    "balanced": 0.50,
    "aggressive": 1.00,
}

MIN_STARTING_VOLUME = 500

# --- Decision engine: sustained vs. transient -----------------------------
# A single day at the CRITICAL threshold is treated as a REDUCE, not a PAUSE
# (principle: one bad metric should not automatically stop the whole
# warm-up). A signal has to stay critical for this many consecutive
# evaluated days before it escalates to PAUSE on its own.
SUSTAINED_CRITICAL_DAYS_FOR_PAUSE = 2

# --- Audience stage sequence ----------------------------------------------
# Fraction of the plan's duration at which each audience stage begins.
# Only used to suggest a starting stage per day; always overridable by the
# user at plan-creation time.
AUDIENCE_STAGE_SEQUENCE = [
    (0.00, "Highly engaged users"),
    (0.15, "Recently engaged users"),
    (0.30, "Active subscribers"),
    (0.60, "Broader audience"),
    (0.85, "Full target audience"),
]
