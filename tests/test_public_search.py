"""No network or credentials; fixtures exercise source failure and evidence reuse."""
import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/wechat-research-assistant/scripts'
sys.path.insert(0, str(SCRIPTS))
import public_search as s

RSS = '<rss><channel><item><title>Example Institute</title><link>https://example.org/about?utm_source=test</link><description>Public background</description></item></channel></rss>'
DDG = '<html><a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fabout">Example <b>Institute</b></a></html>'
GITHUB = json.dumps({'items': [{'full_name': 'fixture/project', 'html_url': 'https://github.com/fixture/project', 'description': 'Public software'}]})


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.p = Path(self.temp.name) / '.private'
        s.secure_private(self.p)

    def tearDown(self):
        self.temp.cleanup()

    def test_rss_html_and_link_normalization(self):
        results, notes = s.parse('bing', RSS, 5)
        self.assertEqual(results[0]['url'], 'https://example.org/about')
        self.assertTrue(results[0]['candidate_only'])
        self.assertFalse(notes)
        ddg, _ = s.parse('ddg', DDG, 5)
        self.assertEqual(ddg[0]['url'], results[0]['url'])
        self.assertEqual(ddg[0]['title'], 'Example Institute')
        self.assertEqual(s.parse('bing', '<rss><channel/></rss>', 5)[0], [])

    def test_challenge_is_not_empty_results(self):
        with self.assertRaises(s.SearchError) as e:
            s.parse('ddg', '<form class="anomaly-modal">challenge</form>', 5)
        self.assertEqual(e.exception.code, 'challenge_or_captcha')
        with self.assertRaises(s.SearchError):
            s.parse('bing', '<html>login required</html>', 5)

    def test_bing_failure_falls_back_and_remains_partial(self):
        def network(url, timeout):
            if 'bing.com' in url:
                raise s.SearchError('network_or_decode_failed')
            self.assertIn('duckduckgo.com', url)
            return DDG, 'text/html', url
        report = s.run_search(self.p, 'Example Institute', ['bing'], transport=network)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual([r['source'] for r in report['sources']], ['bing', 'ddg'])
        self.assertEqual(report['results'][0]['found_by'], ['ddg'])
        self.assertTrue(Path(report['saved_report']).exists())

    def test_partial_source_schema_error_does_not_hide_other_hits(self):
        def network(url, timeout):
            return (GITHUB if 'github.com' in url else '{}'), 'application/json', url
        report = s.run_search(self.p, 'Public software', ['github', 'ror'], transport=network)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(len(report['results']), 1)
        self.assertEqual(report['sources'][1]['error'], 'upstream_schema_changed')

    def test_cache_avoids_request_and_stale_preserves_original_date(self):
        cache = self.p / 'search/cache'
        first = s.search_one('bing', 'Public query', 5, 'en', cache,
                             transport=lambda u, t: (RSS, 'text/xml', u))
        def unavailable(u, t):
            raise s.SearchError('network_or_decode_failed')
        cached = s.search_one('bing', 'Public query', 5, 'en', cache, transport=unavailable)
        self.assertEqual(cached['status'], 'cached')
        stale = s.search_one('bing', 'Public query', 5, 'en', cache, fresh=True, transport=unavailable)
        self.assertEqual(stale['status'], 'stale_cache')
        self.assertEqual(stale['fetched_at'], first['fetched_at'])
        self.assertEqual(stale['results'], first['results'])

    def test_rate_limit_cooldown_applies_to_different_queries(self):
        calls = []
        def limited(u, t):
            calls.append(u)
            raise s.SearchError('rate_limited', 429, wait=1200)
        a = s.search_one('github', 'Query one', 5, 'en', self.p/'search/cache', transport=limited)
        b = s.search_one('github', 'Query two', 5, 'en', self.p/'search/cache', fresh=True, transport=limited)
        self.assertEqual(a['error'], 'rate_limited')
        self.assertEqual(b['error'], 'source_cooling_down')
        self.assertEqual(len(calls), 1)

    def test_queries_reject_secrets_and_transcripts_before_network(self):
        for q in ['密码: fake-secret', '13900001234', '6222 1234 5678 9012',
                  'one line\nsecond line', 'person@example.org', 'wxid_example', '42@chatroom']:
            with self.assertRaises(s.SearchError):
                s.run_search(self.p, q, ['bing'], transport=lambda u, t: self.fail('network called'))

    def test_private_urls_and_redirect_destinations_rejected(self):
        for url in ['http://127.0.0.1/', 'http://localhost/', 'file:///tmp/test', 'https://user:pass@example.org']:
            self.assertFalse(s.clean_url(url))
        addresses = [(2, 1, 6, '', ('10.0.0.1', 443))]
        with patch.object(s.socket, 'getaddrinfo', return_value=addresses):
            with self.assertRaises(s.SearchError):
                s.public_destination('https://example.org/')

    def test_dedup_retains_independent_sources_and_stale_status(self):
        a = s.item('bing', 'Title', 'http://example.org/page')
        b = s.item('ddg', 'Title', 'https://example.org/page')
        combined = s.combine([{'source': 'bing', 'status': 'stale_cache', 'fetched_at': 'old', 'results': [a]},
                              {'source': 'ddg', 'status': 'ok', 'fetched_at': 'new', 'results': [b]}])
        self.assertEqual(len(combined), 1)
        self.assertEqual(combined[0]['found_by'], ['bing', 'ddg'])
        self.assertEqual(combined[0]['observations'][0]['status'], 'stale_cache')

    def test_structured_sources_are_leads_not_identity_assertions(self):
        cr = json.dumps({'message': {'items': [{'DOI': '10.0000/example', 'title': ['A paper'], 'published': {'date-parts': []}}]}})
        r = json.dumps({'items': [{'id': 'https://ror.org/example', 'names': [{'value': 'Institute', 'types': ['ror_display']}],
                                  'links': [{'type': 'website', 'value': 'https://example.org/'}]}]})
        results, _ = s.parse('crossref', cr, 5)
        self.assertTrue(results[0]['candidate_only'])
        org, _ = s.parse('ror', r, 5)
        self.assertEqual(org[0]['official_websites'], ['https://example.org/'])

    def test_sogou_keeps_direct_articles_and_replaces_expiring_links(self):
        page = ('<h3><a uigs="article_title_0" href="/link?url=opaque&amp;token=temporary">A <em>public</em> article</a></h3>'
                '<h3><a id="sogou_vr_11002601_title_1" href="https://mp.weixin.qq.com/center/ignored">Unrecognised path</a></h3>'
                '<h3><a uigs="article_title_2" href="https://mp.weixin.qq.com/s?mid=123">Direct article</a></h3>')
        rows, _ = s.parse('sogou_wechat', page, 5)
        self.assertEqual([row['link_kind'] for row in rows], ['title_search', 'title_search', 'article'])
        self.assertIn('A+public+article', rows[0]['url'])
        self.assertNotIn('temporary', json.dumps(rows))

    def test_openalex_and_gleif_keep_primary_evidence_links(self):
        org, _ = s.parse('openalex', json.dumps({'results': [{'display_name': 'Example University',
            'id': 'https://openalex.org/I123', 'homepage_url': 'https://example.org/', 'ror': 'https://ror.org/123'}]}), 1)
        lei = 'ABCDEFGHIJKLMNOP1234'
        org_lei, _ = s.parse('gleif', json.dumps({'data': [{'id': lei, 'attributes': {
            'entity': {'legalName': {'name': 'Example Company'}, 'jurisdiction': 'US'},
            'registration': {'status': 'ISSUED'}}}]}), 1)
        self.assertEqual(org[0]['official_websites'], ['https://example.org/'])
        self.assertEqual(org_lei[0]['url'], 'https://api.gleif.org/api/v1/lei-records/' + lei)
        self.assertTrue(org_lei[0]['candidate_only'])

    def test_configured_search_instances_have_separate_caches(self):
        calls = []
        def network(url, timeout):
            calls.append(url)
            return '{"results":[]}', 'application/json', url
        cache = self.p / 'search/cache'
        missing = s.search_one('searxng', 'Public test', 1, 'en', cache, transport=network)
        self.assertEqual(missing['status'], 'not_configured')
        for port in (8080, 8081, 8080):
            s.search_one('searxng', 'Public test', 1, 'en', cache, transport=network,
                         searxng_url=f'http://127.0.0.1:{port}')
        self.assertEqual(len(calls), 2)
        self.assertIn('format=json', calls[0])
        with self.assertRaises(s.SearchError):
            s.instance_base('http://192.168.1.1:8080')
        with self.assertRaises(s.SearchError):
            s.InstanceRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org/')

    def test_atom_feed_relative_links_and_rsshub_route_validation(self):
        atom = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>v1.122.0</title>'
                '<link href="https://example.org/item"/><link rel="self" href="/api/item"/>'
                '</entry></feed>')
        with patch.object(s, 'request', return_value=(atom, 'application/atom+xml', 'http://127.0.0.1:1200/news')) as req:
            feed = s.fetch_feed(self.p, '/news', 2, 5, 'http://127.0.0.1:1200')
            self.assertEqual(feed['results'][0]['url'], 'https://example.org/item')
            self.assertEqual(feed['results'][0]['title'], 'v1.122.0')
            req.assert_called_once_with('http://127.0.0.1:1200/news', 5, instance=True)
        with self.assertRaises(s.SearchError):
            s.fetch_feed(self.p, '//example.org/news', 2, 5, 'http://127.0.0.1:1200')

    def test_chain_separates_identity_strength_from_claim_support(self):
        subject = 'Example Person'
        quotes = {
            'https://example.org/staff': 'Example Person led Project Cedar at Example University.',
            'https://news.example.org/staff': 'Example University appointed Example Person to lead Project Cedar.',
            'https://example.net/awards': 'Project Cedar was led by Example Person of Example University.',
            'https://example.com/announcement': 'At Example University, Example Person did not lead Project Cedar.',
            'https://example.com/profile': 'Example Person states that they led Project Cedar.',
            'https://example.edu/about': 'Example University runs Project Cedar.',
            'https://example.edu/crew': 'Example Person joined Example University.',
            'https://example.net/archive': 'Example Person led Project Cedar at Example University.',
        }
        for url, quote in quotes.items():
            digest = hashlib.sha256(url.encode()).hexdigest()
            s.atomic_write(self.p / 'search/pages' / (digest + '.json'), {'status': 'ok', 'url': url,
                           'text': quote, 'evidence_id': 'page-' + digest[:20], 'fetched_at': '2026-11-20T22:00:00+08:00'})

        def evidence(url, kind='primary', stance='supports', **extra):
            return {'url': url, 'kind': kind, 'stance': stance, 'quote': quotes[url], **extra}

        primary = evidence('https://example.org/staff')
        examples = {
            'primary': [primary],
            'corroborated': [primary, evidence('https://example.net/awards')],
            'duplicate': [primary, evidence('https://news.example.org/staff'), evidence('https://example.net/archive'),
                          evidence('https://example.net/awards', 'reprint', origin_url=primary['url'])],
            'conflicting': [primary, evidence('https://example.com/announcement', stance='contradicts')],
            'self': [evidence('https://example.com/profile', 'self_report')],
            'unresolved': [evidence('https://example.edu/about'), evidence('https://example.edu/crew')],
        }
        data = {'subject': subject, 'anchors': [{'kind': 'organization', 'value': 'Example University'},
                {'kind': 'project', 'value': 'Project Cedar'}, {'kind': 'profile_url', 'value': 'https://example.com/profile'}],
                'claims': [{'id': key, 'statement': 'Led Project Cedar', 'evidence': rows} for key, rows in examples.items()]}
        report = s.build_chain(self.p, data, s.query_guard, s.clean_url)
        self.assertEqual([claim['status'] for claim in report['claims']], ['primary_support',
            'independent_primary_support', 'primary_support', 'conflicting', 'self_reported', 'identity_unresolved'])
        self.assertEqual([row['identity']['status'] for row in report['claims'][-1]['evidence']],
                         ['context_only', 'same_name_candidate'])
        self.assertTrue(Path(report['saved_report']).is_file())
        data['claims'][0]['evidence'][0]['quote'] = 'This was never published.'
        with self.assertRaises(s.SearchError) as e:
            s.build_chain(self.p, data, s.query_guard, s.clean_url)
        self.assertEqual(e.exception.code, 'chain_quote_absent_from_snapshot')

    def test_canvas_export_keeps_reports_private_and_data_inert(self):
        source = self.p / 'search/chains/example.json'
        s.atomic_write(source, {'subject': '</script><script>example()</script>', 'claims': []})
        exported = s.export_canvas(self.p, source)
        path = Path(exported['saved_canvas'])
        self.assertEqual(path.parent, self.p / 'search/canvases')
        html = path.read_text(encoding='utf-8')
        self.assertIn(r'\u003c/script>\u003cscript>example()', html)
        self.assertNotIn('</script><script>example()</script>', html)
        self.assertEqual(s.export_canvas(self.p)['reports'], 1)
        with self.assertRaises(s.SearchError):
            s.export_canvas(self.p, path)

    def test_chain_records_search_provenance_and_checked_empty_runs(self):
        def transport(url, timeout):
            return GITHUB, 'application/json', url

        run = s.run_search(self.p, 'Public project', ['github'], transport=transport)
        empty = s.run_search(self.p, 'Other project', ['github'],
            transport=lambda url, timeout: ('{"items":[]}', 'application/json', url))
        url = run['results'][0]['url']
        quote = 'Example Person maintains the public project for Example University.'
        digest = hashlib.sha256(url.encode()).hexdigest()
        s.atomic_write(self.p / 'search/pages' / (digest + '.json'), {'status': 'ok', 'url': url,
            'text': quote, 'evidence_id': 'page-' + digest[:20], 'fetched_at': '2026-11-20T22:00:00+08:00'})
        evidence = {'url': url, 'kind': 'primary', 'stance': 'supports', 'quote': quote,
            'discovery': {'run_id': run['run_id'], 'candidate_id': run['results'][0]['evidence_id']}}
        data = {'subject': 'Example Person', 'investigation_id': 'example-project', 'anchors': [
            {'kind': 'organization', 'value': 'Example University'}, {'kind': 'project', 'value': 'public project'}],
            'search_runs': [run['run_id'], empty['run_id']], 'claims': [
                {'id': 'unresolved', 'statement': 'An open question', 'evidence': []},
                {'id': 'result', 'statement': 'Maintains the project', 'depends_on': ['unresolved'], 'evidence': [evidence]}]}
        result = s.build_chain(self.p, data, s.query_guard, s.clean_url)
        self.assertEqual(result['investigation_id'], 'example-project')
        self.assertEqual([log['candidate_count'] for log in result['search_runs']], [1, 0])
        self.assertEqual(result['claims'][1]['depends_on'], ['unresolved'])
        self.assertTrue(result['claims'][1]['evidence'][0]['discovery']['exact_url_match'])
        evidence['discovery']['run_id'] = empty['run_id']
        with self.assertRaisesRegex(s.SearchError, 'chain_search_candidate_missing'):
            s.build_chain(self.p, data, s.query_guard, s.clean_url)


if __name__ == '__main__':
    unittest.main()
