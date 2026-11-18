import os
from typing import Optional, Dict, Any, List

import requests


JIRA_BASE_URL = os.getenv('JIRA_BASE_URL', '').rstrip('/')
JIRA_USER_EMAIL = os.getenv('JIRA_USER_EMAIL', '')
JIRA_API_TOKEN = os.getenv('JIRA_API_TOKEN', '')
JIRA_PROJECT_KEY = os.getenv('JIRA_PROJECT_KEY', 'DEL')
JIRA_PRIORITY = os.getenv('JIRA_PRIORITY', 'Medium')

_jira_account_id: Optional[str] = None


def _jira_headers() -> Dict[str, str]:
    return {
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }


def _jira_auth() -> Optional[tuple]:
    if not (JIRA_USER_EMAIL and JIRA_API_TOKEN):
        return None
    return (JIRA_USER_EMAIL, JIRA_API_TOKEN)


def _require_jira_config() -> None:
    missing = []
    if not JIRA_BASE_URL:
        missing.append('JIRA_BASE_URL')
    if not JIRA_USER_EMAIL:
        missing.append('JIRA_USER_EMAIL')
    if not JIRA_API_TOKEN:
        missing.append('JIRA_API_TOKEN')
    if missing:
        raise RuntimeError(f'Missing Jira config: {", ".join(missing)}')


def get_jira_account_id(force_refresh: bool = False) -> str:
    global _jira_account_id
    _require_jira_config()
    if _jira_account_id and not force_refresh:
        return _jira_account_id

    auth = _jira_auth()
    if not auth:
        raise RuntimeError('Missing Jira auth credentials')

    url = f'{JIRA_BASE_URL}/rest/api/3/user/search'
    params = {'query': JIRA_USER_EMAIL}
    response = requests.get(url, headers=_jira_headers(), params=params, auth=auth, timeout=20)
    if response.status_code != 200:
        raise RuntimeError(f'Jira user search failed: {response.status_code} {response.text[:200]}')
    users = response.json()
    if not users:
        raise RuntimeError('Jira user search returned no results')
    _jira_account_id = users[0].get('accountId')
    if not _jira_account_id:
        raise RuntimeError('Jira accountId not found in response')
    return _jira_account_id


def create_jira_ticket(
    issue_type: str,
    summary: str,
    description: str
) -> Dict[str, Any]:
    _require_jira_config()
    auth = _jira_auth()
    if not auth:
        raise RuntimeError('Missing Jira auth credentials')

    account_id = get_jira_account_id()
    url = f'{JIRA_BASE_URL}/rest/api/3/issue'
    adf_description = {
        'type': 'doc',
        'version': 1,
        'content': [
            {
                'type': 'paragraph',
                'content': [
                    {
                        'type': 'text',
                        'text': description
                    }
                ]
            }
        ]
    }
    payload = {
        'fields': {
            'project': {'key': JIRA_PROJECT_KEY},
            'issuetype': {'name': issue_type},
            'summary': summary,
            'description': adf_description,
            'assignee': {'accountId': account_id},
            'reporter': {'accountId': account_id},
            'priority': {'name': JIRA_PRIORITY}
        }
    }
    response = requests.post(url, headers=_jira_headers(), json=payload, auth=auth, timeout=20)
    if response.status_code not in (200, 201):
        raise RuntimeError(f'Jira create failed: {response.status_code} {response.text[:300]}')
    return response.json()


# ==========================================================================
# Read-only access (context for the Observatory UI and the agent)
#
# Everything below only issues GET requests. DEL moved to the BlueConic Jira
# site (blueconic.atlassian.net) on 13 Oct 2026, so JIRA_BASE_URL /
# JIRA_USER_EMAIL / JIRA_API_TOKEN must point at that site and account.
# ==========================================================================

import re
import time
from datetime import datetime, timezone

_READ_FIELDS = [
    'summary', 'status', 'issuetype', 'priority', 'assignee', 'reporter',
    'created', 'updated', 'resolutiondate', 'labels', 'components',
]
_CACHE_TTL = int(os.getenv('JIRA_CACHE_TTL', '300'))
_cache: Dict[str, tuple] = {}


def _cached(key: str, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _CACHE_TTL:
        return hit[1]
    value = fn()
    _cache[key] = (time.time(), value)
    return value


def clear_jira_cache() -> None:
    _cache.clear()


def adf_to_text(node: Any) -> str:
    """Flatten Atlassian Document Format to plain text."""
    if node is None:
        return ''
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return ''.join(adf_to_text(n) for n in node)
    t = node.get('type')
    if t == 'text':
        return node.get('text', '')
    if t == 'hardBreak':
        return '\n'
    if t in ('mention', 'emoji'):
        return node.get('attrs', {}).get('text', '')
    if t in ('inlineCard', 'blockCard'):
        return node.get('attrs', {}).get('url', '')
    inner = adf_to_text(node.get('content', []))
    if t == 'listItem':
        return f'• {inner.strip()}\n'
    if t in ('paragraph', 'heading', 'codeBlock', 'blockquote', 'tableRow'):
        return inner.rstrip() + '\n'
    return inner


def _browse_url(key: str) -> str:
    return f'{JIRA_BASE_URL}/browse/{key}' if JIRA_BASE_URL else ''


def simplify_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    """Reduce a raw Jira issue to the fields Observatory uses."""
    f = issue.get('fields', {}) or {}
    status = f.get('status') or {}
    return {
        'key': issue.get('key'),
        'url': _browse_url(issue.get('key', '')),
        'summary': f.get('summary') or '',
        'status': status.get('name'),
        'status_category': (status.get('statusCategory') or {}).get('key'),  # new / indeterminate / done
        'type': (f.get('issuetype') or {}).get('name'),
        'priority': (f.get('priority') or {}).get('name'),
        'assignee': (f.get('assignee') or {}).get('displayName'),
        'reporter': (f.get('reporter') or {}).get('displayName'),
        'created': f.get('created'),
        'updated': f.get('updated'),
        'resolved': f.get('resolutiondate'),
        'labels': f.get('labels') or [],
        'components': [c.get('name') for c in (f.get('components') or [])],
    }


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    _require_jira_config()
    response = requests.get(f'{JIRA_BASE_URL}{path}', headers=_jira_headers(),
                            params=params, auth=_jira_auth(), timeout=30)
    if response.status_code == 401:
        raise RuntimeError('Jira rejected the credentials (401). Check JIRA_USER_EMAIL / '
                           'JIRA_API_TOKEN are for the site in JIRA_BASE_URL.')
    if response.status_code != 200:
        raise RuntimeError(f'Jira GET {path} failed: {response.status_code} {response.text[:200]}')
    return response.json()


def search_issues(jql: str, max_results: int = 50, fields: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Run a JQL search (paginated) and return simplified issues."""
    out: List[Dict[str, Any]] = []
    token = None
    while len(out) < max_results:
        params = {
            'jql': jql,
            'maxResults': min(100, max_results - len(out)),
            'fields': ','.join(fields or _READ_FIELDS),
        }
        if token:
            params['nextPageToken'] = token
        data = _get('/rest/api/3/search/jql', params)
        out.extend(simplify_issue(i) for i in data.get('issues', []))
        token = data.get('nextPageToken')
        if data.get('isLast', True) or not token:
            break
    return out


def get_issue(key: str, max_comments: int = 10) -> Dict[str, Any]:
    """One issue with description and its most recent comments."""
    key = key.strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9]+-\d+', key):
        raise ValueError(f'{key!r} is not a Jira issue key')
    data = _get(f'/rest/api/3/issue/{key}', {'fields': ','.join(_READ_FIELDS + ['description', 'comment'])})
    issue = simplify_issue(data)
    f = data.get('fields', {}) or {}
    issue['description'] = adf_to_text(f.get('description')).strip()
    cfield = f.get('comment') or {}
    comments = cfield.get('comments', [])
    issue['comment_count'] = cfield.get('total', len(comments))
    if issue['comment_count'] > len(comments) or len(comments) > max_comments:
        # The issue payload holds only the first page of comments; fetch the newest directly.
        try:
            newest = _get(f'/rest/api/3/issue/{key}/comment',
                          {'orderBy': '-created', 'maxResults': max_comments}).get('comments', [])
            comments = list(reversed(newest))
        except Exception:
            pass
    issue['comments'] = [
        {'author': (c.get('author') or {}).get('displayName'),
         'created': c.get('created'),
         'body': adf_to_text(c.get('body')).strip()}
        for c in comments[-max_comments:]
    ]
    return issue


def _jql_quote(term: str) -> str:
    return term.replace('\\', '\\\\').replace('"', '\\"')


def find_tickets(term: str, include_closed: bool = True, max_results: int = 25) -> List[Dict[str, Any]]:
    """DEL tickets mentioning a domain, account or phrase (summary, description, comments)."""
    term = (term or '').strip()
    if not term:
        return []
    q = _jql_quote(term)
    jql = f'project = {JIRA_PROJECT_KEY} AND (summary ~ "\\"{q}\\"" OR text ~ "\\"{q}\\"")'
    if not include_closed:
        jql += ' AND statusCategory != Done'
    jql += ' ORDER BY updated DESC'
    return _cached(f'find:{jql}:{max_results}', lambda: search_issues(jql, max_results))


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def summarize_issues(open_issues: List[Dict[str, Any]], recent_issues: List[Dict[str, Any]],
                     stale_days: int = 30, now: Optional[datetime] = None, weeks: int = 12) -> Dict[str, Any]:
    """Pure aggregation (no I/O) so it can be tested on fixture data."""
    now = now or datetime.now(timezone.utc)

    def count(items, field):
        c: Dict[str, int] = {}
        for i in items:
            k = i.get(field) or 'Unassigned' if field == 'assignee' else i.get(field) or '—'
            c[k] = c.get(k, 0) + 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1]))

    def age_days(i, field):
        ts = _parse_ts(i.get(field))
        return (now - ts).days if ts else None

    for i in open_issues:
        i['age_days'] = age_days(i, 'created')
        i['idle_days'] = age_days(i, 'updated')

    stale = sorted([i for i in open_issues if i['idle_days'] is not None and i['idle_days'] >= stale_days],
                   key=lambda i: -(i['idle_days'] or 0))
    def within(i, field, days):
        a = age_days(i, field)
        return a is not None and a < days

    created_7 = [i for i in recent_issues if within(i, 'created', 7)]
    created_30 = [i for i in recent_issues if within(i, 'created', 30)]
    resolved_30 = [i for i in recent_issues if within(i, 'resolved', 30)]

    # Weekly flow (Mon-start weeks, oldest first, current partial week last)
    from datetime import timedelta
    week0 = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    starts = [week0 - timedelta(weeks=k) for k in range(weeks - 1, -1, -1)]

    def bucket(field):
        counts = [0] * weeks
        for i in recent_issues:
            ts = _parse_ts(i.get(field))
            if not ts or ts < starts[0]:
                continue
            idx = min(int((ts - starts[0]).days // 7), weeks - 1)
            counts[idx] += 1
        return counts

    created_prev_30 = sum(1 for i in recent_issues
                          if (lambda a: a is not None and 30 <= a < 60)(age_days(i, 'created')))
    day_counts: Dict[str, int] = {}
    for i in resolved_30:
        d = (i.get('resolved') or '')[:10]
        day_counts[d] = day_counts.get(d, 0) + 1
    peak_day, peak_n = max(day_counts.items(), key=lambda kv: kv[1]) if day_counts else (None, 0)

    age_edges = [(0, 7, '≤7d'), (8, 30, '8–30d'), (31, 90, '1–3mo'), (91, 365, '3–12mo'), (366, 10**6, '1y+')]
    age_buckets = [{'label': lbl, 'count': sum(1 for i in open_issues
                                               if i['age_days'] is not None and lo <= i['age_days'] <= hi)}
                   for lo, hi, lbl in age_edges]

    return {
        'project': JIRA_PROJECT_KEY,
        'site': JIRA_BASE_URL,
        'generated_at': now.isoformat(),
        'weekly': {'week_starts': [w.date().isoformat() for w in starts],
                   'created': bucket('created'), 'resolved': bucket('resolved')},
        'created_prev_30d': created_prev_30,
        'resolved_peak_day': {'date': peak_day, 'count': peak_n},
        'unassigned_open': sum(1 for i in open_issues if not i.get('assignee')),
        'oldest_idle_days': max((i['idle_days'] for i in open_issues if i['idle_days'] is not None), default=None),
        'open_age_buckets': age_buckets,
        'open_total': len(open_issues),
        'in_progress': sum(1 for i in open_issues if (i.get('status') or '').lower() == 'in progress'),
        'created_last_7d': len(created_7),
        'created_last_30d': len(created_30),
        'resolved_last_30d': len(resolved_30),
        'stale_days': stale_days,
        'stale_total': len(stale),
        'open_by_status': count(open_issues, 'status'),
        'open_by_type': count(open_issues, 'type'),
        'open_by_assignee': count(open_issues, 'assignee'),
        'created_30d_by_type': count(created_30, 'type'),
        'stale': stale[:25],
        'open': sorted(open_issues, key=lambda i: i.get('updated') or '', reverse=True),
        'recent': [i for i in recent_issues if within(i, 'created', 30)][:25],
    }


def get_del_summary(stale_days: int = 30, force_refresh: bool = False) -> Dict[str, Any]:
    """Dashboard-style snapshot of the DEL project."""
    if force_refresh:
        clear_jira_cache()
    p = JIRA_PROJECT_KEY

    def build():
        open_issues = search_issues(f'project = {p} AND statusCategory != Done ORDER BY updated DESC', 500)
        # 12 weeks back, to cover the weekly flow chart and the previous-30d comparison.
        recent = search_issues(
            f'project = {p} AND (created >= -{12 * 7 + 7}d OR resolutiondate >= -{12 * 7 + 7}d) '
            f'ORDER BY created DESC', 1000)
        return summarize_issues(open_issues, recent, stale_days)

    return _cached(f'summary:{p}:{stale_days}', build)


# --- Filtered browsing (DEL Tickets page table) ---------------------------

_ORDERS = {
    'updated_desc': 'updated DESC', 'updated_asc': 'updated ASC',
    'created_desc': 'created DESC', 'created_asc': 'created ASC',
}


def _last_comment(issue_key: str, comment_field: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Newest comment. Search returns only the first page (20) of comments, so for
    tickets with more than that, fetch the newest one directly."""
    comments = comment_field.get('comments') or []
    total = comment_field.get('total', len(comments))
    if not total:
        return None
    if total > len(comments):
        try:
            data = _get(f'/rest/api/3/issue/{issue_key}/comment',
                        {'orderBy': '-created', 'maxResults': 1})
            comments = data.get('comments') or comments
            c = comments[0]
        except Exception:
            c = comments[-1]
    else:
        c = comments[-1]
    return {'author': (c.get('author') or {}).get('displayName'),
            'created': c.get('created'),
            'body': adf_to_text(c.get('body')).strip()[:1500],
            'total': total}


def browse_issues(status: str = 'open', issue_type: str = '', q: str = '',
                  stale_days: int = 0, created_within_days: int = 0,
                  order: str = 'updated_desc', limit: int = 100) -> Dict[str, Any]:
    """DEL tickets filtered for the UI table, each with its last comment.

    status: 'open' (anything not done), 'all', or an exact status name
            ('Completed', 'In Progress', 'MASTER TICKETS', ...).
    """
    clauses = [f'project = {JIRA_PROJECT_KEY}']
    s = (status or 'open').strip()
    if s.lower() == 'open':
        clauses.append('statusCategory != Done')
    elif s.lower() != 'all':
        clauses.append(f'status = "{_jql_quote(s)}"')
    if issue_type:
        clauses.append(f'issuetype = "{_jql_quote(issue_type)}"')
    if q and q.strip():
        qq = _jql_quote(q.strip())
        clauses.append(f'(summary ~ "\\"{qq}\\"" OR text ~ "\\"{qq}\\"")')
    if stale_days and stale_days > 0:
        clauses.append(f'updated <= -{int(stale_days)}d')
    if created_within_days and created_within_days > 0:
        clauses.append(f'created >= -{int(created_within_days)}d')
    jql = ' AND '.join(clauses) + ' ORDER BY ' + _ORDERS.get(order, _ORDERS['updated_desc'])
    limit = max(1, min(int(limit), 300))

    def build():
        raw: List[Dict[str, Any]] = []
        token = None
        while len(raw) < limit:
            params = {'jql': jql, 'maxResults': min(100, limit - len(raw)),
                      'fields': ','.join(_READ_FIELDS + ['comment'])}
            if token:
                params['nextPageToken'] = token
            data = _get('/rest/api/3/search/jql', params)
            raw.extend(data.get('issues', []))
            token = data.get('nextPageToken')
            if data.get('isLast', True) or not token:
                break
        now = datetime.now(timezone.utc)
        issues = []
        for r in raw:
            i = simplify_issue(r)
            i['last_comment'] = _last_comment(r.get('key'), (r.get('fields') or {}).get('comment') or {})
            u = _parse_ts(i.get('updated'))
            i['idle_days'] = (now - u).days if u else None
            issues.append(i)
        return {'jql': jql, 'count': len(issues), 'truncated': len(raw) >= limit, 'issues': issues}

    return _cached(f'browse:{jql}:{limit}', build)


def get_del_meta() -> Dict[str, Any]:
    """Statuses and issue types configured on the DEL project (for filter dropdowns)."""
    def build():
        data = _get(f'/rest/api/3/project/{JIRA_PROJECT_KEY}/statuses')
        statuses: Dict[str, str] = {}
        for t in data:
            for st in t.get('statuses', []):
                statuses[st['name']] = (st.get('statusCategory') or {}).get('key')
        cat_order = {'new': 0, 'indeterminate': 1, 'done': 2}
        return {
            'types': sorted({t['name'] for t in data}),
            'statuses': [{'name': n, 'category': c} for n, c in
                         sorted(statuses.items(), key=lambda kv: (cat_order.get(kv[1], 9), kv[0]))],
        }
    return _cached(f'meta:{JIRA_PROJECT_KEY}', build)


# --- Comments posted as the END USER (never with the server's own token) -----
#
# The caller passes the person's own Atlassian email + API token. They are used
# for this one request and never stored or logged. There is deliberately no
# fallback to JIRA_USER_EMAIL / JIRA_API_TOKEN: a comment must be authored by
# whoever is using Observatory, not by the account the server runs as.

def _user_auth(email: str, token: str) -> tuple:
    email, token = (email or '').strip(), (token or '').strip()
    if not email or not token:
        raise PermissionError('Connect your own Jira account (email + API token) to comment.')
    return (email, token)


def whoami(email: str, token: str) -> Dict[str, Any]:
    """Verify a user's Jira credentials; returns their Jira identity."""
    if not JIRA_BASE_URL:
        raise RuntimeError('Missing Jira config: JIRA_BASE_URL')
    r = requests.get(f'{JIRA_BASE_URL}/rest/api/3/myself', headers=_jira_headers(),
                     auth=_user_auth(email, token), timeout=20)
    if r.status_code in (401, 403):
        raise PermissionError(f'Jira did not accept that email + API token for {JIRA_BASE_URL}.')
    if r.status_code != 200:
        raise RuntimeError(f'Jira /myself failed: {r.status_code}')
    me = r.json()
    return {'account_id': me.get('accountId'), 'name': me.get('displayName'),
            'email': me.get('emailAddress') or email.strip(), 'site': JIRA_BASE_URL}


def _text_to_adf(text: str) -> Dict[str, Any]:
    paragraphs = []
    for block in re.split(r'\n\s*\n', text.strip()):
        content: List[Dict[str, Any]] = []
        for n, line in enumerate(block.split('\n')):
            if n:
                content.append({'type': 'hardBreak'})
            if line:
                content.append({'type': 'text', 'text': line})
        paragraphs.append({'type': 'paragraph', 'content': content})
    return {'type': 'doc', 'version': 1, 'content': paragraphs}


COMMENT_FOOTER = 'comment left via Observatory'


def _with_footer(doc: Dict[str, Any], colour: bool = True) -> Dict[str, Any]:
    """Append a subtle italic grey 'comment left via Observatory' line."""
    marks: List[Dict[str, Any]] = [{'type': 'em'}]
    if colour:
        marks.append({'type': 'textColor', 'attrs': {'color': '#97a0af'}})
    doc['content'].append({'type': 'paragraph',
                           'content': [{'type': 'text', 'text': COMMENT_FOOTER, 'marks': marks}]})
    return doc


def add_comment_as_user(key: str, body: str, email: str, token: str) -> Dict[str, Any]:
    key = (key or '').strip().upper()
    if not re.fullmatch(r'[A-Z][A-Z0-9]+-\d+', key):
        raise ValueError(f'{key!r} is not a Jira issue key')
    if not key.startswith(f'{JIRA_PROJECT_KEY}-'):
        raise ValueError(f'Comments are only allowed on {JIRA_PROJECT_KEY} tickets')
    body = (body or '').strip()
    if not body:
        raise ValueError('Comment is empty')
    if len(body) > 10000:
        raise ValueError('Comment is too long (max 10,000 characters)')
    url = f'{JIRA_BASE_URL}/rest/api/3/issue/{key}/comment'
    auth = _user_auth(email, token)
    r = requests.post(url, headers=_jira_headers(), json={'body': _with_footer(_text_to_adf(body))},
                      auth=auth, timeout=20)
    if r.status_code == 400:
        # If this Jira rejects the colour mark, fall back to a plain italic footer.
        r = requests.post(url, headers=_jira_headers(),
                          json={'body': _with_footer(_text_to_adf(body), colour=False)}, auth=auth, timeout=20)
    if r.status_code in (401, 403):
        raise PermissionError('Jira rejected your credentials, or you do not have permission to comment on this ticket.')
    if r.status_code == 404:
        # Jira answers 404 (not 401) when the credentials are wrong; tell the two apart.
        whoami(email, token)  # raises PermissionError on bad credentials
        raise ValueError(f'{key} not found (or not visible to your Jira account)')
    if r.status_code not in (200, 201):
        raise RuntimeError(f'Jira comment failed: {r.status_code} {r.text[:200]}')
    c = r.json()
    clear_jira_cache()  # updated date + last comment changed
    return {'author': (c.get('author') or {}).get('displayName'), 'created': c.get('created'),
            'body': adf_to_text(c.get('body')).strip()}
