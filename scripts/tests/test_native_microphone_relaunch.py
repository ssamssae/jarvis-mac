"""The app relaunches itself when in-process microphone recovery has failed (T-261019-012).

2026-10-19 on Athena: after a temporary aggregate input was emptied under a running engine,
this process's CoreAudio client kept naming removed devices ("no object with given ID",
"AudioDeviceCreateIOProcID: no device with given ID"). Every rebuild failed for minutes, with
or without naming the device explicitly; a fresh process recovered at once each time.
"""
from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

NATIVE = Path(__file__).resolve().parents[1]/'native'/'jarvis_mac_listener.swift'


class MicrophoneRelaunchSourceTests(unittest.TestCase):
    def test_relaunch_helper_leaves_the_app_process_group_and_records_first(self):
        source = NATIVE.read_text()
        body = source[source.index('private func relaunchForMicrophone() -> Bool {'):]
        body = body[:body.index('\n    }\n')]
        self.assertIn('POSIX_SPAWN_SETSID', body)  # launchd may reap the app's process group on exit
        self.assertLess(body.index('last-microphone-relaunch.json'), body.index('posix_spawn('))
        self.assertLess(body.index('posix_spawn('), body.index('NSApp.terminate'))

    def test_microphone_start_failure_at_launch_is_recovered_not_final(self):
        source = NATIVE.read_text()
        start = source[source.index('AVCaptureDevice.requestAccess(for: .audio)'):]
        start = start[:start.index('\n        }\n')]
        self.assertNotIn('마이크 시작 실패', start)


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift required')
class NativeMicrophoneRelaunchTests(unittest.TestCase):
    def test_command_reopens_the_bundle_only_after_the_old_process_exits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root/'opened'
            fake_open = root/'open'
            fake_open.write_text(f'#!/bin/sh\necho "$@" > {marker}\n')
            fake_open.chmod(0o755)
            old_app = subprocess.Popen(['sleep', '1.5'])
            source = root/'main.swift'
            binary = root/'relaunch-command'
            source.write_text(NATIVE.read_text() + r'''
let arguments = MicrophoneRelaunch.command(pid: Int32(CommandLine.arguments[1])!, bundle: "/Applications/Fake Jarvis.app",
                                           open: CommandLine.arguments[2])
print(String(data: try! JSONSerialization.data(withJSONObject: arguments), encoding: .utf8)!)
''')
            compiled = subprocess.run(['swiftc', '-swift-version', '5', '-D', 'VAD_TEST', str(source), '-o', str(binary)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            printed = subprocess.run([str(binary), str(old_app.pid), str(fake_open)], capture_output=True, text=True, timeout=30)
            self.assertEqual(printed.returncode, 0, printed.stderr)
            helper = subprocess.Popen(['/bin/sh'] + json.loads(printed.stdout))
            time.sleep(0.6)
            self.assertFalse(marker.exists(), 'opened while the old app was still running')
            old_app.wait(timeout=5)
            helper.wait(timeout=5)
            self.assertEqual(marker.read_text().strip(), '-g -a /Applications/Fake Jarvis.app')
