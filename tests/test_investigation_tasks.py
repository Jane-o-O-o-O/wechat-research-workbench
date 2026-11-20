import io
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/wechat-research-assistant/scripts'))
import investigation_tasks as tasks


class FinishedProcess:
    def __init__(self, answer='{"claims": []}'):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message',
            'text': answer}}) + '\n{"type":"turn.completed"}\n')
        self.returncode = 0

    def poll(self):
        return self.returncode


class TaskTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.private = Path(temporary.name) / '.private'
        self.state = tasks.create_task(self.private, {'subject': 'Example Institute', 'objective': 'Find its website',
            'keywords': ['Example Institute'], 'profile_url': 'https://example.org/', 'public_search_authorized': True})
        self.directory = tasks.task_dir(self.private, self.state['id'])

    def gather(self, directory, request, stop, record):
        self.assertEqual(request['keywords'], ['Example Institute'])
        url = 'https://example.org/'
        text = 'Example Institute publishes research.'
        key = hashlib.sha256(url.encode()).hexdigest()
        tasks.atomic_write(directory / 'search/pages' / (key + '.json'), {'status': 'ok', 'url': url,
            'text': text, 'evidence_id': 'page-' + key[:20], 'fetched_at': tasks.now()})
        return [], [{'url': url, 'text': text}], {}

    def test_unfetched_sources_rejected(self):
        request = tasks.read_json(self.directory / 'request.json')
        with self.assertRaisesRegex(tasks.WorkflowError, 'codex_review_unfetched_source'):
            tasks.validated_report(self.directory, request, [], [], {},
                '{"claims":[{"evidence":[{"url":"https://example.net/"}]}]}')

    def test_new_report_is_published_under_stable_id_and_survives_retry(self):
        def stage(workspace, request):
            return FinishedProcess(json.dumps({'claims': [{'id': 'result', 'statement': 'Publishes research',
                'evidence': [{'url': 'https://example.org/', 'quote': 'Example Institute publishes research.',
                    'kind': 'primary', 'stance': 'supports'}]}]}))

        state = tasks.run_task(self.private, self.state['id'], launch=stage, gather=self.gather)
        self.assertEqual((state['status'], state['findings']), ('completed', 1))
        report = self.private / 'search' / 'chains' / (self.state['id'] + '.json')
        self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['investigation_id'], self.state['id'])

        def offline(workspace, request):
            raise tasks.WorkflowError('codex_cli_not_found')

        state = tasks.run_task(self.private, self.state['id'], launch=offline, gather=self.gather)
        self.assertEqual((state['status'], state['attempt']), ('failed', 2))
        self.assertTrue(report.exists())

    def test_cancel_stops_current_process(self):
        process = FinishedProcess()
        process.returncode = None

        def stage(workspace, request):
            tasks.stop_task(self.private, self.state['id'])
            return process

        def stop(current):
            self.assertIs(current, process)
            current.returncode = 1

        state = tasks.run_task(self.private, self.state['id'], launch=stage, stop_process=stop, gather=self.gather)
        self.assertEqual(state['status'], 'stopped')
