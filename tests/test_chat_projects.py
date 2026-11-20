import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/wechat-research-assistant/scripts'))
import chat_projects as p
import chat_library as library
import workflow as w


class ProjectReader:
    def __init__(self):
        self.read = []

    def account(self):
        return 'test-account'

    def call(self, *args):
        if args != ('chat', 'contacts', '--parsed'):
            raise AssertionError('unexpected metadata request')
        return [{'username': 'contact-a', 'displayName': 'Test contact'},
                {'username': 'case@chatroom', 'displayName': 'Test group'},
                {'username': 'other@chatroom', 'displayName': 'Unselected group'}]

    def page(self, contact, size, cursor, since):
        if contact not in ('contact-a', 'case@chatroom'):
            raise AssertionError('conversation was outside the project')
        self.read.append(contact)
        if cursor:
            return []
        return [{'_cursor': 'after-' + contact, 'localId': '1', 'shard': '0', 'serverId': '200',
                 'localType': '1', 'createTime': '1767344400', 'content': 'Friday delivery 周五交付 联系人',
                 'sender': 'fixture-sender-account', 'isSend': False}]


class ProjectTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.private = Path(temp.name) / '.private'
        w.atomic_write(self.private / 'settings.json', {'reader_executable': 'fixture', 'self_wxid': 'test-account',
            'initial_since': '2026-01-01T00:00:00+08:00', 'conversations': [], 'max_pages': 5})
        self.reader = ProjectReader()
        result = p.save_project(self.private, {'name': 'Delivery', 'objective': 'Check Friday scope',
            'since': '2026-01-01', 'sources': ['contact-a', 'case@chatroom']}, reader=self.reader)
        self.id = result['id']
        self.root = p.location(self.private, self.id)
        self.request = {'revision': w.read_json(self.root / 'project.json')['revision'], 'model_authorized': True}

    def test_selected_chats_are_combined_and_evidence_stays_attributed(self):
        self.assertEqual(self.reader.read, [])

        def reviewer(workspace, prompt):
            request = json.loads(prompt.split('\n', 1)[1])
            self.assertNotIn('fixture-sender-account', prompt)
            self.assertEqual([source['kind'] for source in request['conversations']], ['direct', 'group'])
            return {'summary': 'Friday appears in both chats.', 'findings': [{'title': 'Friday delivery',
                'detail': 'The messages mention Friday.', 'kind': 'fact',
                'evidence_ids': [source['messages'][0]['evidence_id'] for source in request['conversations']]}]}

        result = p.analyze_project(self.private, self.id, self.request, self.reader, reviewer)
        self.assertEqual(result['status'], 'ok')
        report = w.read_json(self.root / 'analysis.json')
        evidence = report['findings'][0]['evidence']
        self.assertEqual({row['source_name'] for row in evidence}, {'Test contact', 'Test group'})
        self.assertNotEqual(evidence[0]['evidence_id'], evidence[1]['evidence_id'])
        self.assertEqual(set(self.reader.read), {'contact-a', 'case@chatroom'})
        self.assertFalse(list((self.root / '.private/pending').glob('*.json')))
        self.assertFalse((self.private / 'state.json').exists())

    def test_consent_and_revision_are_required_before_read(self):
        with self.assertRaisesRegex(w.WorkflowError, 'project_model_authorization_required'):
            p.analyze_project(self.private, self.id, {}, self.reader)
        with self.assertRaisesRegex(w.WorkflowError, 'project_changed_reload_sources'):
            p.analyze_project(self.private, self.id, dict(self.request, revision='expired'), self.reader)
        self.assertEqual(self.reader.read, [])

    def test_read_failure_is_reported_without_running_model(self):
        class TimingOut(ProjectReader):
            def page(self, *args):
                raise w.WorkflowError('reader_timeout')

        def unexpected_review(workspace, prompt):
            self.fail('The model ran after every source failed')

        result = p.analyze_project(self.private, self.id, self.request, TimingOut(), unexpected_review)
        self.assertEqual(result, {'status': 'failed', 'error': 'reader_timeout'})
        self.assertEqual(w.read_json(self.root / 'status.json')['status'], 'failed')
        self.assertFalse((self.root / 'analysis.json').exists())

    def test_import_resumes_and_search_is_scoped_to_one_chat(self):
        project = w.read_json(self.root / 'project.json')
        sources = project['conversations']
        settings = p.reader_settings(self.private)

        class Interrupted(ProjectReader):
            def page(self, contact, size, cursor, since):
                self.assert_from_start(since)
                if cursor:
                    raise w.WorkflowError('reader_timeout')
                return super().page(contact, size, cursor, since)

            @staticmethod
            def assert_from_start(since):
                if since != 0:
                    raise AssertionError('library must request the reader history from the beginning')

        reader = Interrupted()
        first = library.sync_sources(self.private, settings, sources[:1], reader)
        self.assertEqual(first[0]['error'], 'reader_timeout')
        self.assertEqual(reader.read, ['contact-a'])

        class Resuming(ProjectReader):
            def page(self, contact, size, cursor, since):
                if contact == 'contact-a':
                    if cursor == 'after-contact-a-2':
                        return []
                    if cursor != 'after-contact-a':
                        raise AssertionError('committed cursor was not restored')
                    first, = super().page(contact, size, None, since)
                    second = dict(first, _cursor='after-contact-a-2', localId='2', serverId='201',
                                  createTime='1767344401', content='另一条消息')
                    return [second, first]
                return super().page(contact, size, cursor, since)

        restored = library.sync_sources(self.private, settings, sources, Resuming())
        self.assertEqual([row['status'] for row in restored], ['ready', 'ready'])
        library.sync_sources(self.private, settings, sources, Resuming())
        with library.open_library(self.private, settings['self_wxid']) as db:
            self.assertEqual({row['name']: row['total'] for row in library.listing(db)}, {'Test contact': 2, 'Test group': 1})
            for source in sources:
                for term in ('周五', '周五交付'):
                    result = library.search(db, settings['self_wxid'], source['slug'], {'keyword': term})
                    self.assertEqual(len(result['messages']), 1)
                    self.assertTrue(result['messages'][0]['evidence_id'].startswith(source['slug']))
            total, date_filtered = library.recent(db, sources[0]['slug'], 1767344401, 80)
            self.assertEqual(total, 1)
            self.assertEqual(date_filtered[0]['content'], '另一条消息')
        with self.assertRaisesRegex(w.WorkflowError, 'library_account_changed'):
            with library.open_library(self.private, 'other-account'):
                self.fail('the archive was opened under a different account')

    def test_contacts_cache_is_used_for_reader_failure_but_not_account_mismatch(self):
        expected = p.conversation_catalog(self.private, self.reader)
        self.assertFalse(expected['cached'])

        class Unavailable(ProjectReader):
            def call(self, *args):
                raise w.WorkflowError('reader_command_failed')

        cached = p.conversation_catalog(self.private, Unavailable())
        self.assertTrue(cached['cached'])
        self.assertEqual(cached['conversations'], expected['conversations'])
        self.assertEqual(cached['updated_at'], expected['updated_at'])

        class DifferentAccount(Unavailable):
            def account(self):
                return 'other-account'

        with self.assertRaisesRegex(w.WorkflowError, 'reader_account_mismatch'):
            p.conversation_catalog(self.private, DifferentAccount())
