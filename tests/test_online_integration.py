import io
import json
import pathlib
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'portable'))
import server
import tools_runtime


class OnlineIntegrationTests(unittest.TestCase):
    def job(self, role='analysis', **extra):
        return {'_role': role, '_cancel': threading.Event(), **extra}

    def local_response(self):
        return io.BytesIO((json.dumps({'message': {'content': 'Local answer'}, 'done': True})+'\n').encode())

    def test_chat_capability_accepts_ready_hybrid_route_without_local_model(self):
        online = {'mode': 'hybrid', 'routes': [
            {'provider': 'groq', 'model': 'example', 'roles': ['chat'], 'ready': True},
        ]}
        self.assertTrue(server.chat_capability_available([], online))
        self.assertFalse(server.chat_capability_available([], {'mode': 'local', 'routes': online['routes']}))
        self.assertFalse(server.chat_capability_available([], {'mode': 'hybrid', 'routes': [
            {'provider': 'groq', 'model': 'example', 'roles': ['analysis'], 'ready': True},
        ]}))
        self.assertFalse(server.chat_capability_available([], {'mode': 'hybrid', 'routes': [
            {'provider': 'groq', 'model': 'example', 'roles': ['chat'], 'ready': False},
        ]}))
        self.assertTrue(server.chat_capability_available([{'name': 'qwen3:8b'}], {'mode': 'local', 'routes': []}))

    def test_remote_answer_does_not_start_ollama_and_records_provenance(self):
        job = self.job()
        with patch.object(server.ONLINE, 'complete', return_value={'text':'Remote answer','provider':'groq','model':'example','usage':{}}), patch.object(server.urllib.request,'urlopen') as local:
            self.assertEqual(server.call_model([{'role':'user','content':'Compare these options'}], job=job), 'Remote answer')
            local.assert_not_called()
            self.assertEqual(job['inference']['provider'], 'groq')

    def test_no_available_provider_falls_back_to_real_local_path(self):
        job = self.job()
        with patch.object(server.ONLINE, 'complete', side_effect=server.OnlineUnavailable('Not available')), patch.object(server.urllib.request,'urlopen',return_value=self.local_response()) as local:
            self.assertEqual(server.call_model([{'role':'user','content':'Compare'}], model='local-test', job=job), 'Local answer')
            self.assertEqual(job['inference']['location'], 'local')
            self.assertEqual(json.loads(local.call_args.args[0].data)['model'], 'local-test')

    def test_screenshots_and_structured_actions_never_reach_cloud(self):
        for messages, job in [([{'role':'user','content':'See screen','images':['private-image']}], self.job()), ([{'role':'user','content':[{'type':'image_url','image_url':'https://example.invalid/private.png'}]}], self.job()), ([{'role':'user','content':'Choose an action'}], self.job('router', _format='json')), ([{'role':'user','content':'Remote Discord message'}], self.job(_local_only=True))]:
            with self.subTest(job=job), patch.object(server.ONLINE, 'complete') as online, patch.object(server.urllib.request,'urlopen',return_value=self.local_response()):
                server.call_model(messages, model='local-test', job=job)
                online.assert_not_called()

    def test_online_fleet_uses_distinct_assigned_models_and_actual_fallback_names(self):
        first, second = 'online:groq:first', 'online:nvidia:second'
        seen = []
        context = {'online_models':[first, second], 'cancelled': threading.Event(), 'model_results':{}}
        def model_call(messages, model=None):
            seen.append(model)
            context['model_results'][model] = 'actual-provider/' + model.rsplit(':',1)[-1]
            return 'Evidence-based result.'
        context['call_model'] = model_call
        with patch.object(tools_runtime,'_ollama_models') as local_models:
            answer = tools_runtime._worker_fleet('Compare two proposed solutions', [], context)
        local_models.assert_not_called()
        self.assertCountEqual(seen, [first, second, first])
        self.assertIn('analyst actual-provider/first', answer)
        self.assertIn('planner actual-provider/second', answer)


if __name__ == '__main__':
    unittest.main()


class OnlineSettingsAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import ThreadingHTTPServer
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.http.server_port
        cls.thread = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        import tempfile
        from online_models import OnlineRouter
        self.temp = tempfile.TemporaryDirectory()
        self.previous = {
            'data': server.DATA, 'state': server.STATE, 'online': server.ONLINE,
            'jobs': server.JOBS, 'active': server.ACTIVE, 'discord': dict(server.DISCORD),
            'discord_stop': server.DISCORD_STOP, 'discord_thread': server.DISCORD_THREAD,
            'discord_token': server.DISCORD_TOKEN,
        }
        server.DATA = pathlib.Path(self.temp.name)
        server.STATE = {'chats': [], 'profile': '', 'settings': {
            'model': '', 'theme': 'system', 'onboarded': False, 'project_path': '',
            'release_url': '', 'discord': {}, 'online': {'mode': 'local', 'routes': []},
        }}
        server.ONLINE = OnlineRouter()
        server.JOBS = {}
        server.ACTIVE = None
        server.DISCORD.clear()
        server.DISCORD_STOP = threading.Event()
        server.DISCORD_THREAD = None
        server.DISCORD_TOKEN = ''

    def tearDown(self):
        server.DATA = self.previous['data']
        server.STATE = self.previous['state']
        server.ONLINE = self.previous['online']
        server.JOBS = self.previous['jobs']
        server.ACTIVE = self.previous['active']
        server.DISCORD.clear()
        server.DISCORD.update(self.previous['discord'])
        server.DISCORD_STOP = self.previous['discord_stop']
        server.DISCORD_THREAD = self.previous['discord_thread']
        server.DISCORD_TOKEN = self.previous['discord_token']
        self.temp.cleanup()

    def request(self, method, path, body=None, *, session=True, origin=True):
        from http.client import HTTPConnection
        connection = HTTPConnection('127.0.0.1', self.port, timeout=3)
        headers = {'Host': f'127.0.0.1:{self.port}'}
        if session:
            headers['Cookie'] = 'mavi_session=' + server.TOKEN
        if method == 'POST':
            headers['Content-Type'] = 'application/json'
            if origin:
                headers['Origin'] = f'http://127.0.0.1:{self.port}'
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        data = response.read()
        status = response.status
        connection.close()
        return status, json.loads(data)

    def test_hybrid_needs_authenticated_explicit_consent_and_keys_stay_session_only(self):
        config = {'mode': 'hybrid', 'routes': [], 'allow_paid': False}
        secret = 'online-provider-secret-fixture'
        status, _ = self.request('POST', '/api/online/configure',
                                 {'config': config, 'consent': True, 'keys': {'groq': secret}}, session=False)
        self.assertEqual(status, 403)
        self.assertEqual(server.ONLINE.snapshot()['mode'], 'local')

        status, _ = self.request('POST', '/api/online/configure',
                                 {'config': config, 'consent': False, 'keys': {'groq': secret}})
        self.assertEqual(status, 400)
        self.assertEqual(server.ONLINE.snapshot()['mode'], 'local')

        status, response = self.request('POST', '/api/online/configure',
                                        {'config': config, 'consent': True, 'keys': {'groq': secret}})
        self.assertEqual(status, 200)
        self.assertNotIn(secret, json.dumps(response))
        self.assertTrue(next(item for item in response['providers'] if item['id'] == 'groq')['configured'])
        persisted = (server.DATA / 'workspace.json').read_text(encoding='utf-8')
        self.assertNotIn(secret, persisted)
        saved = json.loads(persisted)['settings']['online']
        self.assertEqual(saved, config)

        online_status, state = self.request('GET', '/api/online')
        self.assertEqual(online_status, 200)
        self.assertNotIn(secret, json.dumps(state))
        self.assertEqual(state['config'], config)

        # Startup reload keeps user-selected nonsecret routes but discards all
        # in-memory credentials, including in same-process test reloads.
        server.load()
        self.assertEqual(server.ONLINE.snapshot()['config'], config)
        self.assertFalse(next(item for item in server.ONLINE.snapshot()['providers'] if item['id'] == 'groq')['configured'])

    def test_online_write_requires_same_origin_even_with_session_cookie(self):
        config = {'mode': 'hybrid', 'routes': [], 'allow_paid': False}
        status, _ = self.request('POST', '/api/online/configure',
                                 {'config': config, 'consent': True}, origin=False)
        self.assertEqual(status, 403)
        self.assertEqual(server.ONLINE.snapshot()['mode'], 'local')

    def test_online_status_and_catalog_are_session_gated(self):
        status, _ = self.request('GET', '/api/online', session=False)
        self.assertEqual(status, 403)
        config = {'mode': 'hybrid', 'routes': [], 'allow_paid': False}
        self.assertEqual(self.request('POST', '/api/online/configure',
                                      {'config': config, 'consent': True, 'keys': {'nvidia': 'key-fixture'}})[0], 200)
        status, _ = self.request('POST', '/api/online/catalog', {'provider': 'nvidia'}, session=False)
        self.assertEqual(status, 403)
        with patch.object(server.ONLINE, '_request_json', return_value={'data': [{'id': 'example/model'}]}) as request:
            status, response = self.request('POST', '/api/online/catalog', {'provider': 'nvidia'})
        self.assertEqual(status, 200)
        self.assertEqual(response, {'models': [{'id': 'example/model', 'name': 'example/model'}]})
        self.assertTrue(request.called)
