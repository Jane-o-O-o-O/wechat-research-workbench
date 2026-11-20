"""Additional public data sources for the research CLI."""
from html.parser import HTMLParser
import json
from urllib.parse import urlencode, urljoin, urlsplit
import xml.etree.ElementTree as ET


class SearchError(Exception):
    def __init__(self, code, status=None, wait=900):
        super().__init__(code)
        self.code, self.status, self.wait = code, status, wait


EXTRA_APIS = {
    'openalex': 'https://api.openalex.org/institutions',
    'openalex_authors': 'https://api.openalex.org/authors',
    'gleif': 'https://api.gleif.org/api/v1/lei-records',
    'bluesky': 'https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts',
}
SOGOU = 'https://weixin.sogou.com/weixin'

# These links are displayed locally. The CLI does not send queries to them.
BROWSER_SOURCES = {
    'bilibili': ('social', 'https://search.bilibili.com/all', 'keyword'),
    'weibo': ('social', 'https://s.weibo.com/weibo', 'q'),
    'xiaohongshu': ('social', 'https://www.xiaohongshu.com/search_result', 'keyword'),
    'youtube': ('social', 'https://www.youtube.com/results', 'search_query'),
    'mastodon': ('social', 'https://docs.joinmastodon.org/methods/search/', None),
    'mediacrawler': ('social', 'https://github.com/NanmiCoder/MediaCrawler', None),
    'moe': ('school', 'https://hudong.moe.gov.cn/qggxmd/', None),
    'orcid': ('academic', 'https://orcid.org/orcid-search/search', 'searchQuery'),
    'gsxt': ('company', 'https://www.gsxt.gov.cn/', None),
    'miit': ('company', 'https://beian.miit.gov.cn/', None),
    'qcc': ('company', 'https://openapi.qcc.com/', None),
    'tianyancha': ('company', 'https://open.tianyancha.com/', None),
}


def browser_links(profile, query=None):
    categories = {'organization': ('school', 'company'), 'school': ('school', 'academic'),
                  'academic': ('academic',), 'company': ('company',), 'social': ('social',)}
    links = []
    for source, (category, url, parameter) in BROWSER_SOURCES.items():
        if profile != 'all' and category not in categories.get(profile, ()):
            continue
        if query and parameter:
            url += '?' + urlencode({parameter: query})
        mode = {'qcc': 'commercial_api', 'tianyancha': 'commercial_api',
                'mediacrawler': 'login_based_tool', 'mastodon': 'instance_dependent'}.get(source, 'browser')
        links.append({'source': source, 'category': category, 'mode': mode, 'url': url})
    return links


def sogou_url(q):
    return SOGOU + '?' + urlencode({'type': 2, 'query': q})


class SogouArticles(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_heading = False
        self.article = None
        self.articles = []

    def handle_starttag(self, tag, attrs):
        if tag == 'h3':
            self.in_heading = True
        if tag == 'a' and self.in_heading:
            attrs = dict(attrs)
            if (attrs.get('uigs', '').startswith('article_title_')
                    or attrs.get('id', '').startswith('sogou_vr_11002601_title_')):
                self.article = (attrs.get('href', ''), [])

    def handle_data(self, value):
        if self.article is not None:
            self.article[1].append(value)

    def handle_endtag(self, tag):
        if tag == 'a' and self.article is not None:
            link, parts = self.article
            self.articles.append((' '.join(''.join(parts).split()), urljoin(SOGOU, link)))
            self.article = None
        if tag == 'h3':
            self.in_heading = False


def parse_sogou(body, limit, item, redact):
    parser = SogouArticles()
    parser.feed(body)
    if not parser.articles and not any(x in body for x in ('no-result', '未找到相关', '没有找到相关')):
        raise SearchError('search_markup_changed_or_blocked')
    results = []
    for title, link in parser.articles[:limit]:
        if not title:
            continue
        dest = urlsplit(link)
        article_path = dest.path in ('/s', '/mp/appmsg/show') or dest.path.startswith('/s/')
        if dest.hostname == 'mp.weixin.qq.com' and article_path:
            results.append(item('sogou_wechat', title, link, link_kind='article'))
        else:
            # Sogou's opaque redirects expire and often contain transient tokens.
            results.append(item('sogou_wechat', title, sogou_url('"' + redact(title)[:160] + '"'),
                                link_kind='title_search'))
    return results


def endpoint(source, q, limit, lang, searxng_url=None):
    if source == 'sogou_wechat':
        return sogou_url(q)
    if source in ('openalex', 'openalex_authors'):
        fields = {'openalex': 'id,display_name,homepage_url,ror,country_code',
                  'openalex_authors': 'id,display_name,orcid,last_known_institutions'}
        query = {'search': q, 'per_page': limit, 'select': fields[source]}
    elif source == 'gleif':
        query = {'filter[entity.legalName]': q, 'page[size]': limit}
    elif source == 'bluesky':
        query = {'q': q, 'limit': limit}
    elif source == 'searxng':
        if not searxng_url:
            raise SearchError('searxng_instance_not_configured')
        return searxng_url + '/search?' + urlencode({'q': q, 'format': 'json', 'language': lang})
    else:
        raise SearchError('unknown_source')
    return EXTRA_APIS[source] + '?' + urlencode(query)


def parse_json(source, body, limit, item, clean_url):
    data = json.loads(body)
    if source == 'openalex':
        results = []
        for row in data['results'][:limit]:
            website = clean_url(row.get('homepage_url'))
            results.append(item(source, row['display_name'], row['id'], row.get('country_code'),
                                official_websites=[website] if website else [], ror=clean_url(row.get('ror'))))
        return results, []
    if source == 'openalex_authors':
        return [item(source, row['display_name'], row['id'],
                     ' / '.join(org['display_name'] for org in row.get('last_known_institutions', [])),
                     orcid=clean_url(row.get('orcid'))) for row in data['results'][:limit]], []
    if source == 'gleif':
        results = []
        for row in data['data'][:limit]:
            lei = row['id']
            entity = row['attributes']['entity']
            if len(lei) != 20 or not lei.isascii() or not lei.isalnum():
                raise ValueError('invalid LEI')
            registry_url = EXTRA_APIS['gleif'] + '/' + lei
            results.append(item(source, entity['legalName']['name'], registry_url,
                                entity.get('jurisdiction'), lei=lei, registry_url=registry_url,
                                registration_status=row['attributes']['registration']['status']))
        return results, []
    if source == 'bluesky':
        results = []
        for row in data['posts'][:limit]:
            uri = row['uri']
            prefix = 'at://' + row['author']['did'] + '/app.bsky.feed.post/'
            if not uri.startswith(prefix) or not uri[len(prefix):].replace('-', '').isalnum():
                raise ValueError('invalid post URI')
            handle = row['author']['handle']
            results.append(item(source, row['author'].get('displayName') or handle,
                                'https://bsky.app/profile/' + row['author']['did'] + '/post/' + uri[len(prefix):],
                                row['record']['text'], published=row['record'].get('createdAt')))
        return results, []
    if source == 'searxng':
        notes = ['upstream_incomplete_results'] if data.get('unresponsive_engines') else []
        return [item(source, row['title'], row['url'], row.get('content'))
                for row in data['results'][:limit]], notes
    raise SearchError('unknown_source')


def parse_feed(body, url, limit, item):
    if '<!doctype' in body.lower():
        raise SearchError('unexpected_feed_markup')
    root = ET.fromstring(body)
    if root.tag == 'rss' and root.find('channel') is not None:
        return [item('rss', row.findtext('title'), urljoin(url, row.findtext('link') or ''),
                     row.findtext('description'), published=row.findtext('pubDate'))
                for row in root.findall('./channel/item')[:limit] if row.findtext('link')]
    ns = {'a': 'http://www.w3.org/2005/Atom'}
    if root.tag == '{' + ns['a'] + '}feed':
        rows = []
        for row in root.findall('a:entry', ns)[:limit]:
            for link in row.findall('a:link', ns):
                if link.get('rel', 'alternate') == 'alternate' and link.get('href'):
                    rows.append(item('rss', row.findtext('a:title', namespaces=ns), urljoin(url, link.get('href')),
                                     row.findtext('a:summary', namespaces=ns),
                                     published=row.findtext('a:updated', namespaces=ns)))
                    break
        return rows
    raise SearchError('unexpected_feed_markup')
