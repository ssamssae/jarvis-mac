"""Voice selection changes synthesis configuration, never model instructions."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jarvis_mac_voice as voice

spec = importlib.util.spec_from_file_location('jarvis_installer', Path(__file__).resolve().parents[1]/'install-jarvis-mac-listener.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)
VOICES = ('Yuna                  ko_KR    # 안녕하세요\n'
          'Eddy (Korean (Korea))  ko_KR    # 안녕하세요\n'
          '유나 고품질 (한국어)       ko_KR    # 안녕하세요\n')


class VoiceResolutionTests(unittest.TestCase):
    def test_exact_installed_names_preserve_spaces_parentheses_and_unicode(self):
        with patch.object(voice.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, VOICES)) as run:
            for name in ('Yuna', 'Eddy (Korean (Korea))', '유나 고품질 (한국어)'):
                self.assertEqual(voice.resolve_say_voice(name), name)
            for name in ('Eddy', '유나', 'yuna', 'Yuna missing'):
                with self.assertRaisesRegex(ValueError, 'voice_not_installed'):
                    voice.resolve_say_voice(name)
            self.assertEqual(run.call_args.args[0], ['/usr/bin/say', '-v', '?'])

    def test_omission_preserves_setting_or_backward_default_without_listing(self):
        with patch.object(voice.subprocess, 'run') as run:
            self.assertEqual(voice.resolve_say_voice(), 'Yuna')
            self.assertEqual(voice.resolve_say_voice(existing='Selected (Korean)'), 'Selected (Korean)')
            run.assert_not_called()
            for blank in ('', '  ', '\t'):
                with self.assertRaisesRegex(ValueError, 'voice_cannot_be_blank'):
                    voice.resolve_say_voice(blank)
            run.assert_not_called()


class InstallerVoiceTests(unittest.TestCase):
    def install_fixture(self, requested=None, existing=None):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            root = home/'Library/Application Support/JarvisMacOSS'
            root.mkdir(parents=True)
            if existing is not None:
                (root/'config.json').write_text(json.dumps({'voice': existing}))
            model = home/'model.bin'; model.write_bytes(b'model')
            cursor = home/'agent'; cursor.write_text('fixture'); cursor.chmod(0o700)
            argv = ['installer', '--cast-name', 'Speaker', '--model', str(model), '--cursor-binary', str(cursor)]
            if requested is not None: argv.extend(['--voice', requested])
            def command(args, **kwargs):
                return subprocess.CompletedProcess(args, 1 if args[0] == 'pgrep' else 0,
                                                   VOICES if args[0] == '/usr/bin/say' else '')
            with patch.object(sys, 'argv', argv), patch.object(sys, 'platform', 'darwin'), \
                 patch.object(installer.Path, 'home', return_value=home), \
                 patch.object(installer.shutil, 'which', return_value='/fixture/whisper-cli'), \
                 patch.object(installer.subprocess, 'run', side_effect=command), \
                 patch.object(sys, 'stdout', io.StringIO()):
                installer.main()
            saved = json.loads((root/'config.json').read_text())
            self.assertEqual((root/'config.json').stat().st_mode & 0o777, 0o600)
            return saved

    def test_explicit_voice_is_persisted(self):
        self.assertEqual(self.install_fixture('유나 고품질 (한국어)', 'Yuna')['voice'], '유나 고품질 (한국어)')

    def test_update_without_option_preserves_existing_voice(self):
        self.assertEqual(self.install_fixture(existing='Selected (Korean)')['voice'], 'Selected (Korean)')

    def test_first_install_without_option_defaults_to_yuna(self):
        self.assertEqual(self.install_fixture()['voice'], 'Yuna')


if __name__ == '__main__': unittest.main()
