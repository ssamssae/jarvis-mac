"""JarvisMacOSS.app is a regular Dock app: running dot, top-left app menu, bundled icon."""
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import unittest

NATIVE = Path(__file__).resolve().parents[1]/'native'


class DockAppBundleTests(unittest.TestCase):
    def test_info_plist_is_not_menubar_only_and_names_icon(self):
        info = plistlib.loads((NATIVE/'jarvis_mac_listener.Info.plist').read_bytes())
        self.assertNotEqual(info.get('LSUIElement'), True)
        self.assertEqual(info.get('CFBundleIconFile'), 'JarvisMacOSS')
        self.assertTrue((NATIVE/'JarvisMacOSS.icns').is_file())

    def test_installer_bundles_the_icon(self):
        installer = (NATIVE.parent/'install-jarvis-mac-listener.py').read_text()
        self.assertIn("'native/JarvisMacOSS.icns'", installer)
        self.assertIn("'Resources'", installer)


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift required')
class NativeDockAppTests(unittest.TestCase):
    def test_regular_policy_menu_and_reopen(self):
        fixture = r'''
func require(_ condition: Bool, _ name: String) { if !condition { fatalError(name) } }
_ = NSApplication.shared
require(Listener.restingActivationPolicy == .regular, "regular policy")
let menu = Listener.makeMainMenu(target: nil)
let items = menu.items.compactMap { $0.submenu }.flatMap { $0.items }
require(items.contains { $0.action == #selector(Listener.showStatusWindow) }, "status item")
require(items.contains { $0.action == #selector(Listener.togglePause) }, "pause item")
require(items.contains { $0.action == #selector(Listener.quitApp) && $0.keyEquivalent == "q" }, "quit item")
require(menu.items.contains { $0.submenu?.title == "윈도우" }, "window menu")
require(Listener.instancesRespond(to: #selector(NSApplicationDelegate.applicationShouldHandleReopen(_:hasVisibleWindows:))), "reopen")
print("DOCK_APP_OK")
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'main.swift'; binary = root/'test'
            source.write_text((NATIVE/'jarvis_mac_listener.swift').read_text() + fixture)
            compiled = subprocess.run(['swiftc', '-swift-version', '5', '-D', 'VAD_TEST', str(source), '-o', str(binary)],
                                      capture_output=True, text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr[-2000:])
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr[-2000:])
            self.assertIn('DOCK_APP_OK', result.stdout)


if __name__ == '__main__':
    unittest.main()
