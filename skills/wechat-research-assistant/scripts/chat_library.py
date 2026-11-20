"""Local, account-bound archive and searchable index for selected chats."""
import argparse
from contextlib import closing, contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

from workflow import WorkflowError, body_of, lock, message_key, normalize_row, now, review_of, verify_account


SOURCE_ID = re.compile(r'chat-[0-9a-f]{24}\Z')
DB_NAME = 'conversations.sqlite3'
SCHEMA = """
CREATE TABLE IF NOT EXISTS owner (slot INTEGER PRIMARY KEY CHECK (slot = 1), account TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chats (
    slug TEXT PRIMARY KEY, wxid TEXT NOT NULL UNIQUE, name TEXT NOT NULL, kind TEXT NOT NULL,
    cursor TEXT, status TEXT NOT NULL, error TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, slug TEXT NOT NULL REFERENCES chats(slug), message_key TEXT NOT NULL,
    timestamp INTEGER NOT NULL, raw TEXT NOT NULL, body TEXT NOT NULL, UNIQUE (slug, message_key)
);
CREATE INDEX IF NOT EXISTS message_time ON messages(slug, timestamp DESC, id DESC);
"""


def source_slug(identifier):
    return 'chat-' + hashlib.sha256(identifier.encode()).hexdigest()[:24]


@contextmanager
def open_library(private, account, *, read_only=False):
    path = private / DB_NAME
    target = path.as_uri() + '?mode=ro' if read_only else path
    with closing(sqlite3.connect(target, uri=read_only, timeout=8)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        if not read_only and not db.execute("SELECT 1 FROM sqlite_master WHERE name = 'owner'").fetchone():
            with db:
                db.executescript('BEGIN IMMEDIATE;\n' + SCHEMA)
                db.execute('INSERT OR IGNORE INTO owner VALUES (1, ?)', (account,))
        owner = db.execute('SELECT account FROM owner WHERE slot = 1').fetchone()
        if owner is None or owner[0] != account:
            raise WorkflowError('library_account_changed')
        yield db


def enable_index(db):
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'message_search'").fetchone():
        return True
    try:
        with db:
            db.execute('BEGIN')
            db.execute("CREATE VIRTUAL TABLE message_search USING fts5(body, tokenize='trigram')")
            db.execute('INSERT INTO message_search(rowid, body) SELECT id, body FROM messages')
    except sqlite3.OperationalError as error:
        if 'no such module' not in str(error) and 'no such tokenizer' not in str(error):
            raise
        return False
    return True


def registered(db, slug):
    if not isinstance(slug, str) or not SOURCE_ID.fullmatch(slug):
        raise WorkflowError('library_conversation_unavailable')
    chat = db.execute('SELECT * FROM chats WHERE slug = ?', (slug,)).fetchone()
    if chat is None:
        raise WorkflowError('library_conversation_unavailable')
    return chat


def selected_sources(request, catalog):
    selected = request.get('sources') if isinstance(request, dict) else None
    if (not isinstance(selected, list) or not 1 <= len(selected) <= 20
            or any(not isinstance(item, str) or not item for item in selected)
            or len(set(selected)) != len(selected)):
        raise WorkflowError('select_one_to_twenty_conversations')
    available = {source['id']: source for source in catalog}
    if any(source not in available for source in selected):
        raise WorkflowError('library_conversation_unavailable')
    return [dict(available[source], slug=source_slug(source)) for source in selected]


def save_page(db, slug, rows, cursor, indexed):
    with db:
        for row in rows:
            body, _ = body_of(row)
            result = db.execute(
                'INSERT OR IGNORE INTO messages(slug, message_key, timestamp, raw, body) VALUES (?, ?, ?, ?, ?)',
                (slug, message_key(row), int(row.get('createTime') or 0), json.dumps(row, ensure_ascii=False), body))
            if indexed and result.rowcount:
                db.execute('INSERT INTO message_search(rowid, body) VALUES (?, ?)', (result.lastrowid, body))
        db.execute('UPDATE chats SET cursor = ?, updated_at = ? WHERE slug = ?', (cursor, now(), slug))


def import_chat(db, settings, slug, reader, indexed):
    chat = registered(db, slug)
    cursor = chat['cursor']
    seen = {cursor} if cursor else set()
    try:
        verify_account(reader, settings)
        with db:
            db.execute("UPDATE chats SET status = 'importing', error = NULL, updated_at = ? WHERE slug = ?", (now(), slug))
        for _ in range(int(settings.get('max_pages', 1000))):
            raw = reader.page(chat['wxid'], int(settings.get('page_size', 200)), cursor, 0)
            if not isinstance(raw, list):
                raise WorkflowError('reader_page_not_array')
            verify_account(reader, settings)
            if not raw:
                with db:
                    db.execute("UPDATE chats SET status = 'ready', updated_at = ? WHERE slug = ?", (now(), slug))
                return {'id': slug, 'status': 'ready'}
            rows = [normalize_row(row) for row in raw]
            candidate = rows[0]['_cursor']
            if candidate in seen:
                raise WorkflowError('pagination_not_advancing')
            save_page(db, slug, rows, candidate, indexed)
            seen.add(candidate)
            cursor = candidate
        raise WorkflowError('page_limit_reached_incomplete')
    except (WorkflowError, OSError, ValueError, TypeError, OverflowError, sqlite3.Error) as error:
        code = str(error) if isinstance(error, WorkflowError) else 'library_import_failed'
        with db:
            db.execute("UPDATE chats SET status = 'failed', error = ?, updated_at = ? WHERE slug = ?", (code, now(), slug))
        return {'id': slug, 'status': 'failed', 'error': code}


def sync_sources(private, settings, sources, reader):
    with open_library(private, settings['self_wxid']) as db:
        with db:
            for source in sources:
                db.execute(
                    "INSERT INTO chats(slug, wxid, name, kind, status, updated_at) VALUES (?, ?, ?, ?, 'queued', ?)"
                    ' ON CONFLICT(slug) DO UPDATE SET name=excluded.name, kind=excluded.kind WHERE chats.wxid=excluded.wxid',
                    (source['slug'], source['id'], source['name'], source['kind'], now()))
                if registered(db, source['slug'])['wxid'] != source['id']:
                    raise WorkflowError('library_conversation_unavailable')
        with lock(private):
            indexed = enable_index(db)
            return [import_chat(db, settings, source['slug'], reader, indexed) for source in sources]


def listing(db):
    rows = db.execute("""
        SELECT slug AS id, name, kind, status, error, updated_at,
            (SELECT COUNT(*) FROM messages WHERE slug = chats.slug) AS total,
            (SELECT MIN(timestamp) FROM messages WHERE slug = chats.slug) AS earliest,
            (SELECT MAX(timestamp) FROM messages WHERE slug = chats.slug) AS latest
        FROM chats ORDER BY updated_at DESC, name
    """)
    return [dict(row) for row in rows]


def recent(db, slug, since, limit):
    args = (slug, since)
    total = db.execute('SELECT COUNT(*) FROM messages WHERE slug = ? AND timestamp >= ?', args).fetchone()[0]
    rows = db.execute('SELECT raw FROM messages WHERE slug = ? AND timestamp >= ?'
                      ' ORDER BY timestamp DESC, id DESC LIMIT ?', (*args, limit))
    return total, [json.loads(row['raw']) for row in rows]


def search(db, account, slug, request):
    registered(db, slug)
    if not isinstance(request, dict):
        raise WorkflowError('library_invalid_search')
    keyword, page = request.get('keyword', ''), request.get('page', 0)
    if not isinstance(keyword, str) or len(keyword) > 120 or type(page) is not int or not 0 <= page <= 100000:
        raise WorkflowError('library_invalid_search')
    keyword = keyword.strip()
    args = [slug]
    sql = 'SELECT messages.raw FROM messages WHERE slug = ?'
    if keyword:
        indexed = db.execute("SELECT 1 FROM sqlite_master WHERE name = 'message_search'").fetchone()
        if indexed and len(keyword) >= 3 and '\x00' not in keyword:
            sql += ' AND id IN (SELECT rowid FROM message_search WHERE message_search MATCH ?)'
            args.append('"' + keyword.replace('"', '""') + '"')
        else:
            sql += ' AND instr(lower(body), lower(?)) > 0'
            args.append(keyword)
    size = 30
    rows = db.execute(sql + ' ORDER BY timestamp DESC, id DESC LIMIT ? OFFSET ?', (*args, size + 1, page * size)).fetchall()
    results = []
    for row in rows[:size]:
        evidence = review_of(json.loads(row['raw']), slug, account)
        evidence.pop('sender', None)
        evidence['text'] = evidence['text'][:1600]
        results.append(evidence)
    return {'messages': results, 'page': page, 'has_more': len(rows) > size}


def main(argv=None, emit=None):
    from chat_projects import conversation_directory, location, make_reader, reader_settings
    from workflow import read_json, secure_private

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('list', 'add', 'import-project', 'sync', 'search'))
    parser.add_argument('--workspace', required=True, type=Path)
    parser.add_argument('--id')
    args = parser.parse_args(argv)
    private = args.workspace.resolve() / '.private'
    emit = emit or (lambda data: print(json.dumps(data, ensure_ascii=False)))
    try:
        if args.command == 'list' and not (private / DB_NAME).is_file():
            emit({'status': 'ok', 'conversations': []})
            return 0
        settings = reader_settings(private)
        account = settings['self_wxid']
        if args.command == 'list':
            with open_library(private, account, read_only=True) as db:
                result = {'conversations': listing(db)}
        elif args.command == 'search':
            with open_library(private, account, read_only=True) as db:
                result = search(db, account, args.id, json.load(sys.stdin))
        else:
            secure_private(private)
            reader = make_reader(private, settings)
            if args.command == 'add':
                request = json.load(sys.stdin)
                sources = selected_sources(request, conversation_directory(private, reader))
            elif args.command == 'import-project':
                project = read_json(location(private, args.id) / 'project.json')
                if project['account'] != account:
                    raise WorkflowError('project_account_changed')
                sources = project['conversations']
            else:
                with open_library(private, account, read_only=True) as db:
                    chat = registered(db, args.id)
                    sources = [{'id': chat['wxid'], 'slug': chat['slug'], 'name': chat['name'], 'kind': chat['kind']}]
            outcomes = sync_sources(private, settings, sources, reader)
            result = {'imports': outcomes}
            if all(source['status'] == 'failed' for source in outcomes):
                raise WorkflowError(outcomes[0]['error'])
        emit({'status': 'ok', **result})
        return 0
    except (WorkflowError, OSError, ValueError, TypeError, KeyError, sqlite3.Error) as error:
        emit({'status': 'failed', 'error': str(error) if isinstance(error, WorkflowError) else 'library_operation_failed'})
        return 2
