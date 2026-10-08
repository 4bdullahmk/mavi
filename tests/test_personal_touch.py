import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portable'))
import server


class PersonalTouchTests(unittest.TestCase):
    def test_validation_rejects_invalid_values_without_echo(self):
        for value in ({'trigger': 'hello', 'message': ''}, {'trigger': 1, 'message': 'world'},
                      {'trigger': 'x' * 81, 'message': 'world'},
                      {'trigger': 'hello\n', 'message': 'world'},
                      {'trigger': 'hello', 'message': 'world', 'token': 'private-test'}):
            with self.assertRaises(ValueError) as caught:
                server.validate_personal_touch(value)
            self.assertNotIn('private-test', str(caught.exception))
        self.assertEqual(server.validate_personal_touch({'trigger': ' hello ', 'message': ' world '}),
                         {'trigger': 'hello', 'message': 'world'})
        self.assertEqual(server.validate_personal_touch({'trigger': '', 'message': ''}),
                         {'trigger': '', 'message': ''})

    def test_partial_installer_settings_load_with_defaults_and_local_touch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            touch = {'trigger': 'hello', 'message': 'world'}
            (root / 'workspace.json').write_text(json.dumps({'settings': {'model': 'test-model', 'personal_touch': touch}}))
            with patch.object(server, 'DATA', root), patch.object(server, 'STATE', {'chats': [], 'profile': '', 'settings': {}}), \
                 patch.object(server, 'DISCORD', {}), patch.object(server, 'DISCORD_STOP'), \
                 patch.object(server, 'DISCORD_TOKEN', ''), patch.object(server, 'DISCORD_THREAD', None):
                server.load()
                self.assertEqual(server.STATE['settings']['theme'], 'system')
                self.assertFalse(server.STATE['settings']['onboarded'])
                self.assertEqual(server.STATE['settings']['personal_touch'], touch)
                self.assertEqual(server.STATE['profile'], '')
                self.assertEqual(server.STATE['chats'], [])
                self.assertFalse(server.DISCORD['enabled'])


if __name__ == '__main__':
    unittest.main()
