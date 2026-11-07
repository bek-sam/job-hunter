"""Match confirmation evidence to one requisition, never merely to an employer."""
import datetime as dt
import re
from urllib.parse import urlsplit


def norm(value):
    return re.sub(r'[^a-z0-9]+', ' ', (value or '').lower()).strip()


def requisition(url):
    for pattern in (r'/([0-9a-f]{8}-[0-9a-f-]{27,})(?:/|\?|$)', r'/jobs/(\d+)',
                    r'[_/](R[-_]?\d+)(?:[/?#]|$)', r'[?&](?:job|jobId|gh_jid)=(\w[\w-]+)'):
        m = re.search(pattern, url or '', re.I)
        if m: return m.group(1).lower()
    return ''


def matched(evidence, row, since=None):
    if not evidence.get('found') or not evidence.get('message_id'):
        return False
    if not evidence.get('is_application_confirmation'):
        return False
    title = row.get('Position') or row.get('role') or ''
    company = row.get('Company') or row.get('company') or ''
    url = row.get('Apply Link') or row.get('Link') or row.get('url') or ''
    # Exact requisition evidence is stronger than an inconsistent title or UUID board label.
    req = requisition(url)
    excerpt = evidence.get('excerpt') or ''
    evidence_url = evidence.get('job_url') or ''
    evidence_req = requisition(evidence_url)
    # Parse identifiers from URLs so numeric IDs are compared as whole requisitions
    # (123 must never match 1234 in a message body or neighboring URL).
    same_req = bool(req and (req == evidence_req or
                    re.search(r'(?<![a-z0-9])' + re.escape(req) + r'(?![a-z0-9])', excerpt, re.I)))
    same_title = bool(title and norm(evidence.get('role')) == norm(title) and norm(title) in norm(excerpt))
    employer = norm(company)
    same_company = bool(employer and employer == norm(evidence.get('company')) and employer in norm(excerpt))
    if not same_req and not (same_title and same_company):
        return False
    try:
        received = dt.datetime.fromisoformat(evidence['received_at'].replace('Z', '+00:00'))
        if received.tzinfo is None: return False
        if since:
            start = dt.datetime.fromisoformat(since.replace('Z', '+00:00'))
            if start.tzinfo is None: return False
            if received < start - dt.timedelta(minutes=2): return False
        if received > dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=5): return False
    except (KeyError, TypeError, ValueError):
        return False
    return True


SCHEMA = {'type': 'object', 'required': ['found'], 'properties': {
    'found': {'type': 'boolean'}, 'message_id': {'type': 'string'},
    'is_application_confirmation': {'type': 'boolean'},
    'company': {'type': 'string'}, 'role': {'type': 'string'}, 'job_url': {'type': 'string'},
    'subject': {'type': 'string'}, 'received_at': {'type': 'string'}, 'excerpt': {'type': 'string'}}}


def prompt(row, since=None, historical=False):
    import json
    target = {k: row.get(k) for k in ('Company', 'Position', 'Link', 'Apply Link')}
    return ("Read-only Gmail lookup. Search and READ the actual message, not only snippets. "
            "Find an explicit application-received/submitted confirmation for precisely this job: " + json.dumps(target) +
            f". Search period: {'newer_than:60d' if historical else 'newer_than:2d'}. " +
            (f"It must be received at or after {since} (two-minute clock tolerance). " if since else '') +
            "Return found=false unless the message confirms the EXACT requisition URL/ID, or both exact job title "
            "and employer. A different team, level, specialty, role, login email or marketing acknowledgement does not match. "
            "Never treat any email from the company as proof. Return the real message_id, received_at with timezone, "
            "subject, employer, job title, job URL if present, and an exact short excerpt of the source body containing "
            "the role/employer or requisition ID. Do not create drafts, send, or modify anything. JSON only.")
