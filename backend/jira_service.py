import os
from typing import Optional, Dict, Any

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
