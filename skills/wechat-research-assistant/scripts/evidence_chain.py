"""Assemble reviewed public evidence without fetching anything over the network."""
from datetime import date
import hashlib
import re
import unicodedata
from urllib.parse import urlsplit
import uuid

from research_sources import SearchError
from workflow import atomic_write, now, read_json, redact


ANCHOR_KINDS = {'organization', 'role', 'project', 'date', 'profile_url'}
SOURCE_KINDS = {'primary', 'self_report', 'secondary', 'reprint'}
STANCES = {'supports', 'contradicts', 'context'}


def normalized(value):
    return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())


def mentions(quote, value):
    term = normalized(value)
    if term.isascii():
        return bool(re.search(r'(?<!\w)' + re.escape(term) + r'(?!\w)', normalized(quote)))
    return term in normalized(quote)


def required_text(data, key, limit):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise SearchError('invalid_chain_' + key)
    return value.strip()


def records(data, key):
    values = data.get(key)
    if not isinstance(values, list) or any(not isinstance(value, dict) for value in values):
        raise SearchError('invalid_chain_' + key)
    return values


def identifiers(data, key):
    values = data.get(key, [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise SearchError('invalid_chain_' + key)
    return list(dict.fromkeys(values))


def load_search_runs(private, data):
    summaries, candidates = [], {}
    for run_id in identifiers(data, 'search_runs'):
        if not re.fullmatch(r'\d{8}T\d{6}-[a-f0-9]{8}', run_id):
            raise SearchError('invalid_chain_search_run_id')
        path = private / 'search' / 'runs' / (run_id + '.json')
        if not path.is_file() or path.is_symlink():
            raise SearchError('chain_search_run_missing')
        run = read_json(path)
        if (not isinstance(run, dict) or run.get('run_id', run_id) != run_id
                or run.get('operation', 'search') != 'search'):
            raise SearchError('invalid_chain_search_run')
        rows = records(run, 'results')
        summaries.append({'run_id': run_id, 'query': required_text(run, 'query', 240),
                          'queried_at': required_text(run, 'queried_at', 80), 'status': run.get('status'),
                          'candidate_count': len(rows), 'sources': [
                              {'name': source.get('source'), 'status': source.get('status'),
                               'candidate_count': len(records(source, 'results'))}
                              for source in records(run, 'sources')]})
        candidates[run_id] = {row.get('evidence_id'): row for row in rows}
    return summaries, candidates


def public_url(data, key, clean_url):
    raw = required_text(data, key, 2000)
    url = clean_url(raw)
    if not url or not urlsplit(url).hostname:
        raise SearchError('invalid_chain_' + key)
    return url


def read_anchor(anchor, query_guard, clean_url):
    kind = required_text(anchor, 'kind', 20)
    if kind not in ANCHOR_KINDS:
        raise SearchError('invalid_chain_anchor_kind')
    value = (public_url(anchor, 'value', clean_url) if kind == 'profile_url'
             else query_guard(required_text(anchor, 'value', 160)))
    return {'kind': kind, 'value': value}


def identity_link(subject, anchors, quote, url):
    matches = [a for a in anchors if (a['value'] == url if a['kind'] == 'profile_url'
                                      else mentions(quote, a['value']))]
    axes = {a['kind'] for a in matches} & {'organization', 'role', 'project'}
    name = mentions(quote, subject)
    unique_profile = any(a['kind'] == 'profile_url' for a in matches)
    linked = name and (unique_profile or len(axes) >= 2)
    return {'status': 'linked' if linked else ('same_name_candidate' if name else 'context_only'),
            'name_in_quote': name, 'matched_anchors': matches}


def review_evidence(private, entry, subject, anchors, clean_url, candidates):
    url = public_url(entry, 'url', clean_url)
    quote = required_text(entry, 'quote', 1000)
    kind = required_text(entry, 'kind', 20)
    stance = required_text(entry, 'stance', 20)
    if kind not in SOURCE_KINDS or stance not in STANCES:
        raise SearchError('invalid_chain_evidence_label')
    origin = public_url(entry, 'origin_url', clean_url) if entry.get('origin_url') else url
    if kind == 'reprint' and origin == url:
        raise SearchError('chain_reprint_requires_origin_url')
    digest = hashlib.sha256(url.encode()).hexdigest()
    path = private / 'search' / 'pages' / (digest + '.json')
    if not path.is_file() or path.is_symlink():
        raise SearchError('chain_requires_fetched_original')
    page = read_json(path)
    if (not isinstance(page, dict) or page.get('status') != 'ok' or page.get('url') != url
            or page.get('evidence_id') != 'page-' + digest[:20] or not page.get('fetched_at')):
        raise SearchError('chain_invalid_page_snapshot')
    text = page.get('text')
    literal_quote = ' '.join(quote.split())
    if not isinstance(text, str) or literal_quote not in ' '.join(text.split()):
        raise SearchError('chain_quote_absent_from_snapshot')
    reviewed = {'url': url, 'origin_url': origin, 'kind': kind, 'stance': stance,
            'evidence_id': page['evidence_id'], 'fetched_at': page['fetched_at'],
            'snapshot_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'quote': literal_quote, 'identity': identity_link(subject, anchors, literal_quote, url)}
    discovery = entry.get('discovery')
    if discovery is None:
        return reviewed
    if not isinstance(discovery, dict):
        raise SearchError('invalid_chain_discovery')
    run_id = required_text(discovery, 'run_id', 32)
    candidate_id = required_text(discovery, 'candidate_id', 32)
    candidate = candidates.get(run_id, {}).get(candidate_id)
    if not candidate or not re.fullmatch(r'web-[a-f0-9]{20}', candidate_id):
        raise SearchError('chain_search_candidate_missing')
    candidate_url = public_url(candidate, 'url', clean_url)
    destination = [candidate_url, candidate.get('registry_url'), *candidate.get('official_websites', [])]
    reviewed['discovery'] = {'run_id': run_id, 'candidate_id': candidate_id, 'candidate_url': candidate_url,
                             'source': candidate.get('source'), 'exact_url_match': url in destination}
    return reviewed


def publisher(url):
    labels = urlsplit(url).hostname.rstrip('.').split('.')
    country_suffix = len(labels) > 2 and len(labels[-1]) == 2
    depth = 3 if country_suffix and labels[-2] in {'ac', 'co', 'com', 'edu', 'gov', 'net', 'org'} else 2
    return '.'.join(labels[-depth:])


def count_independent(rows):
    groups = []
    for row in rows:
        keys = {('publisher', publisher(row['url'])), ('publisher', publisher(row['origin_url'])),
                ('quote', normalized(row['quote']))}
        for group in groups[:]:
            if keys & group:
                keys.update(group)
                groups.remove(group)
        groups.append(keys)
    return len(groups)


def assess(evidence):
    linked = [row for row in evidence if row['identity']['status'] == 'linked']
    support = [row for row in linked if row['stance'] == 'supports']
    contradict = [row for row in linked if row['stance'] == 'contradicts']
    primaries = [row for row in support if row['kind'] == 'primary']
    primary_count = count_independent(primaries)
    if support and contradict:
        status = 'conflicting'
    elif any(row['kind'] == 'primary' for row in contradict):
        status = 'primary_contradiction'
    elif primary_count >= 2:
        status = 'independent_primary_support'
    elif primary_count == 1:
        status = 'primary_support'
    elif any(row['kind'] == 'self_report' for row in support):
        status = 'self_reported'
    elif support or contradict:
        status = 'linked_unverified'
    elif evidence and not linked:
        status = 'identity_unresolved'
    else:
        status = 'unverified'
    return status, primary_count


def build_chain(private, data, query_guard, clean_url):
    if not isinstance(data, dict):
        raise SearchError('invalid_chain_input')
    subject = query_guard(required_text(data, 'subject', 120))
    investigation_id = data.get('investigation_id')
    if investigation_id is not None and (not isinstance(investigation_id, str)
            or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', investigation_id)):
        raise SearchError('invalid_chain_investigation_id')
    anchors = [read_anchor(a, query_guard, clean_url) for a in records(data, 'anchors')]
    search_runs, candidates = load_search_runs(private, data)
    claims = []
    seen = set()
    for claim in records(data, 'claims'):
        claim_id = required_text(claim, 'id', 64)
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', claim_id) or claim_id in seen:
            raise SearchError('invalid_or_duplicate_chain_claim_id')
        parents = identifiers(claim, 'depends_on')
        if not all(parent in seen for parent in parents):
            raise SearchError('chain_dependencies_must_precede_claim')
        seen.add(claim_id)
        key_finding = claim.get('key_finding', True)
        if not isinstance(key_finding, bool):
            raise SearchError('invalid_chain_key_finding')
        statement = redact(required_text(claim, 'statement', 500))
        effective_date = claim.get('effective_date')
        if effective_date is not None:
            try:
                if date.fromisoformat(effective_date).isoformat() != effective_date:
                    raise ValueError
            except (TypeError, ValueError):
                raise SearchError('invalid_chain_effective_date') from None
        evidence = [review_evidence(private, row, subject, anchors, clean_url, candidates)
                    for row in records(claim, 'evidence')]
        status, independent_sources = assess(evidence)
        claims.append({'id': claim_id, 'statement': statement, 'effective_date': effective_date,
                       'status': status, 'independent_primary_sources': independent_sources, 'evidence': evidence,
                       'depends_on': parents, 'key_finding': key_finding})
    if not claims and not search_runs:
        raise SearchError('chain_requires_at_least_one_claim')
    report = {'status': 'ok', 'subject': subject, 'confirmed_public_anchors': anchors, 'reviewed_at': now(),
              'source_labels_and_meaning_require_review': True, 'search_runs': search_runs, 'claims': claims}
    if investigation_id is not None:
        report['investigation_id'] = investigation_id
    path = private / 'search' / 'chains' / (uuid.uuid4().hex + '.json')
    atomic_write(path, report)
    return {**report, 'saved_report': str(path)}
