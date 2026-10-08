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
