import io
import json
import pathlib
import sys
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'portable'))
import server


class GenerationPolicyTests(unittest.TestCase):
    def test_local_engine_error_is_actionable_and_redacts_paths(self):
        error = server.urllib.error.HTTPError('http://127.0.0.1/api/chat', 400, 'Bad Request', {}, io.BytesIO(json.dumps({'error': 'input length exceeds context in /' + 'home/example/model'}).encode()))
        with patch.object(server.urllib.request, 'urlopen', side_effect=error):
            with self.assertRaisesRegex(ValueError, 'input length exceeds context') as result:
                server.call_model([{'role': 'user', 'content': 'Test'}], 'qwen3-vl:8b-instruct')
        self.assertNotIn('/' + 'home/example', str(result.exception))

    def test_mac_control_schema_is_forwarded_to_the_model(self):
        from macos_automation import ACTION_SCHEMA, _validate_action
        action = '{"action":"question","text":"Which assignment?"}'
        response = io.BytesIO(json.dumps({'message': {'content': action}, 'done': True}).encode() + b'\n')
        job = {'_cancel': threading.Event(), '_stream': False, '_role': 'router', '_format': ACTION_SCHEMA}
        with patch.object(server.urllib.request, 'urlopen', return_value=response) as request:
            result = server.call_model([{'role': 'system', 'content': 'Choose one app action.'}], 'qwen3-vl:8b-instruct', job)
        self.assertEqual(_validate_action(result)['action'], 'question')
        schema = json.loads(request.call_args.args[0].data)['format']
        self.assertEqual(schema, ACTION_SCHEMA)
        for variant in schema['anyOf']:
            self.assertFalse(variant['additionalProperties'])

    def test_computer_actions_request_json_and_stay_out_of_chat(self):
        action = '{"action":"done","text":"Visible page checked."}'
        response = io.BytesIO(json.dumps({'message': {'content': action}, 'done': True}).encode() + b'\n')
        job = {'_cancel': threading.Event(), '_stream': False, '_role': 'router', '_format': 'json'}
        with patch.object(server.urllib.request, 'urlopen', return_value=response) as request:
            self.assertEqual(server.call_model([{'role': 'system', 'content': 'Return one action JSON object.'}], 'qwen3-vl:8b-instruct', job), action)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['format'], 'json')
        self.assertEqual(payload['options']['temperature'], 0)
        self.assertEqual(payload['options']['num_predict'], 384)
        self.assertNotIn('content', job)

    def test_qwen_orphan_reasoning_is_never_published(self):
        job = {'_cancel': threading.Event(), 'content': ''}
        def response():
            yield json.dumps({'message': {'content': 'Unneeded process detail'}}).encode()
            self.assertEqual(job['content'], '')
            yield json.dumps({'message': {'content': '</think>Done.'}, 'done': True}).encode()
        class Stream:
            def __enter__(self): return response()
            def __exit__(self, *args): pass
        with patch.object(server.urllib.request, 'urlopen', return_value=Stream()) as request:
            self.assertEqual(server.call_model([{'role': 'user', 'content': 'Hello'}], 'qwen3:4b', job), 'Done.')
        payload = json.loads(request.call_args.args[0].data)
        self.assertFalse(payload['think'])
        self.assertEqual(payload['options']['num_predict'], 1536)
        self.assertEqual(job['content'], 'Done.')

    def test_file_budget_and_incomplete_output_rejected(self):
        response = io.BytesIO(json.dumps({'message': {'content': '{"sheets":['}, 'done_reason': 'length'}).encode() + b'\n')
        job = {'_cancel': threading.Event(), '_stream': False, '_role': 'files'}
        with patch.object(server.urllib.request, 'urlopen', return_value=response) as request:
            with self.assertRaisesRegex(ValueError, 'output limit'):
                server.call_model([{'role': 'system', 'content': 'Return workbook JSON only.'}], 'qwen3:8b', job)
        payload = json.loads(request.call_args.args[0].data)
        self.assertEqual(payload['options']['num_predict'], 8192)
        self.assertIn('Return workbook JSON only.', payload['messages'][0]['content'])
        self.assertNotIn('content', job)


if __name__ == '__main__': unittest.main()
