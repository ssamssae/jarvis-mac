"""Real Process/pipe lifecycle using a local fake controller; no mic or providers."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift required')
class NativeControllerRecoveryTests(unittest.TestCase):
    def test_exit_pipe_epoch_pause_and_retry_budget(self):
        native = Path(__file__).resolve().parents[1]/'native/jarvis_mac_listener.swift'
        fixture = r'''
extension Listener {
    func exerciseRecovery(_ root: URL) {
        controllerConfigRoot = root
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        manuallyPaused = true
        func require(_ condition: Bool, _ name: String) {
            if !condition { fatalError(name) }
        }
        func wait(_ seconds: Double, _ condition: () -> Bool) {
            let deadline = Date().addingTimeInterval(seconds)
            while !condition() && Date() < deadline {
                RunLoop.current.run(until: Date().addingTimeInterval(0.02))
            }
            require(condition(), "timeout: ready=\(controllerReady) running=\(child?.isRunning ?? false) recovering=\(recoveringController) count=\(controllerRestartCount) fatal=\(fatalStatus ?? "none")")
        }
        try! launchController()
        wait(5) { self.controllerReady }
        let first = child!.processIdentifier
        // Buffered user commands are never copied to the replacement pipe.
        try! inputPipe!.fileHandleForWriting.write(contentsOf: Data("old-command\n".utf8))
        wait(3) { (try? String(contentsOf: root.appendingPathComponent("received")))?.contains("old-command") == true }
        kill(first, SIGKILL)
        wait(8) { self.controllerRestartCount == 1 && self.controllerReady }
        require(child!.processIdentifier != first, "replacement has a fresh PID")
        require(manuallyPaused && !engine.isRunning, "explicit pause survives recovery")
        require(dictationID.isEmpty && !gateSnapshot().1, "no stale dictation or capture gate")
        // EOF and termination notifications together must consume one restart.
        require(controllerRecovery.attempts == 1, "duplicate callbacks do not consume retries")
        for expected in 2...3 {
            kill(child!.processIdentifier, SIGKILL)
            wait(10) { self.controllerRestartCount == expected && self.controllerReady }
        }
        kill(child!.processIdentifier, SIGKILL)
        wait(6) { self.fatalStatus != nil }
        require(controllerRestartCount == 3 && child == nil, "fourth failure stops the loop")
        require(!controllerReady && !engine.isRunning, "exhaustion stays stopped")
        let records = try! String(contentsOf: root.appendingPathComponent("received"))
        require(records.components(separatedBy: "old-command").count == 2, "old command was not replayed")
        NSStatusBar.system.removeStatusItem(item)
    }
    func exerciseQuitDuringBackoff(_ root: URL) {
        controllerConfigRoot = root
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        manuallyPaused = true
        try! launchController()
        let deadline = Date().addingTimeInterval(5)
        while !controllerReady && Date() < deadline { RunLoop.current.run(until: Date().addingTimeInterval(0.02)) }
        precondition(controllerReady)
        recoverController(reason: "fixture_quit")
        // Quit during delayed restart. Cleanup must finish without scheduling another child.
        let cleanup = Date().addingTimeInterval(5)
        while child != nil && Date() < cleanup { RunLoop.current.run(until: Date().addingTimeInterval(0.02)) }
        precondition(child == nil)
        quitting = true
        let settle = Date().addingTimeInterval(2)
        while Date() < settle { RunLoop.current.run(until: Date().addingTimeInterval(0.02)) }
        precondition(child == nil && controllerRestartCount == 0)
        NSStatusBar.system.removeStatusItem(item)
    }
}
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let listener = Listener()
listener.exerciseRecovery(URL(fileURLWithPath: CommandLine.arguments[1]))
let cancelling = Listener()
cancelling.exerciseQuitDuringBackoff(URL(fileURLWithPath: CommandLine.arguments[2]))
print("native controller recovery passed")
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            worker = root/'controller.py'
            worker.write_text('''import json, os, sys, time
from pathlib import Path
if os.getpgrp() != os.getpid(): os.setsid()
r=Path(sys.argv[2])
(r/'received').touch(exist_ok=True)
print(json.dumps({'state':'listening','listen':True,'process_group':os.getpgrp()}),flush=True)
for line in sys.stdin:
 with (r/'received').open('a') as f: f.write(line)
''')
            (root/'config.json').write_text(json.dumps({'python':sys.executable,'controller':str(worker),'state_dir':str(root)}))
            cancel = root/'cancel'; cancel.mkdir()
            (cancel/'config.json').write_text(json.dumps({'python':sys.executable,'controller':str(worker),'state_dir':str(cancel)}))
            source = root/'main.swift'; binary = root/'test'
            source.write_text(native.read_text() + fixture)
            compiled = subprocess.run(['swiftc','-swift-version','5','-D','VAD_TEST',str(source),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            result = subprocess.run([str(binary),str(root),str(cancel)],capture_output=True,text=True,timeout=50)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('native controller recovery passed',result.stdout)
            receipt = json.loads((root/'last-controller-exit.json').read_text())
            self.assertEqual(receipt['attempt'],3)
            self.assertFalse(receipt['retry_scheduled'])
            self.assertEqual(receipt['exit_status'],9)
            self.assertEqual(receipt['exit_reason'],'signal')
