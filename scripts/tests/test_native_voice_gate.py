"""Exercise the production Swift gate against noisy-room and short-call envelopes."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("swiftc"), "requires macOS Swift")
class NativeVoiceGateTests(unittest.TestCase):
    def test_adaptive_gate(self):
        native = Path(__file__).resolve().parents[1] / "native/jarvis_mac_listener.swift"
        cases = r"""
func feed(_ gate: inout AdaptiveVoiceGate, _ levels: [Double]) -> [Bool] {
    levels.map { gate.isVoiced(db: $0, seconds: 0.02) }
}
func require(_ condition: Bool, _ name: String) {
    if !condition { fatalError(name) }
}
var gate = AdaptiveVoiceGate()
let quiet = Array(repeating: -65.0, count: 200)
require(!feed(&gate, quiet).contains(true), "quiet room stays quiet")
require(feed(&gate, Array(repeating: -35.0, count: 40)).allSatisfy { $0 }, "short wake in quiet room")
require(!feed(&gate, quiet).contains(true), "silence closes wake")

// The previous estimator never learned this noise because every frame was voiced.
var loud = AdaptiveVoiceGate()
_ = feed(&loud, Array(repeating: -42.0, count: 200))
require(!feed(&loud, Array(repeating: -42.0, count: 600)).contains(true), "constant noise must not make repeated 12s clips")
require(abs(loud.noiseDB + 42) < 0.01, "loud ambient baseline learned")
require(feed(&loud, Array(repeating: -28.0, count: 40)).allSatisfy { $0 }, "wake over noisy room survives")
require(!feed(&loud, Array(repeating: -42.0, count: 50)).contains(true), "background after wake supplies 0.8s endpoint")

// A raised floor after capture has begun must converge without unvoiced input.
var changed = AdaptiveVoiceGate()
_ = feed(&changed, quiet)
_ = feed(&changed, Array(repeating: -40.0, count: 200))
require(!feed(&changed, Array(repeating: -40.0, count: 100)).contains(true), "ambient increase recovers")
_ = feed(&changed, quiet)
require(changed.thresholdDB == -48, "quiet floor recovers after ambient falls")
require(feed(&changed, Array(repeating: -44.0, count: 35)).allSatisfy { $0 }, "soft wake in quiet room remains audible")

// Speech with quiet gaps lasts beyond the history window without becoming noise.
var conversation = AdaptiveVoiceGate()
_ = feed(&conversation, Array(repeating: -42.0, count: 200))
for _ in 0..<12 {
    require(feed(&conversation, Array(repeating: -27.0, count: 15)).allSatisfy { $0 }, "syllables preserved")
    _ = feed(&conversation, Array(repeating: -42.0, count: 10))
}
require(!feed(&conversation, Array(repeating: -42.0, count: 50)).contains(true), "question endpoint")
require(!conversation.isVoiced(db: .nan, seconds: 0.02), "invalid level")
print("native voice gate regressions passed")
"""
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.swift"
            binary = Path(directory) / "voice-gate-test"
            source.write_text(native.read_text() + cases)
            subprocess.run(["swiftc", "-swift-version", "5", "-D", "VAD_TEST", str(source), "-o", str(binary)], check=True, capture_output=True, text=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertIn("regressions passed", result.stdout)
