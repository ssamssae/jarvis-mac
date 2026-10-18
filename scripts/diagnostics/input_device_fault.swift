// Reproduce the input-device faults behind T-261019-007/012 on a Mac running Jarvis.
// Build: xcrun swiftc -swift-version 5 scripts/diagnostics/input_device_fault.swift -o /tmp/input_device_fault
//   list                    current default input and every input device (read-only)
//   switch <uid> <seconds>  make another input the default, then restore the original
//   emptydefault <seconds> [settle]
//                           2026-10-18 21:54: wrap the default input in a temporary aggregate,
//                           make it the default for [settle] seconds (default 2.5; restart Jarvis
//                           meanwhile so it captures from the aggregate), empty it, and restore
//                           the original default after <seconds>
//   cleanup                 remove a leftover temporary aggregate
// Watch ~/Library/Application Support/JarvisMacOSS/native-status.json meanwhile. The original
// default input is restored before exit. Property and device changes need no microphone access.
import CoreAudio
import Foundation

let system = AudioObjectID(kAudioObjectSystemObject)
let testUID = "jarvis-input-device-fault-aggregate"

func address(_ selector: AudioObjectPropertySelector,
             _ scope: AudioObjectPropertyScope = kAudioObjectPropertyScopeGlobal) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: scope, mElement: kAudioObjectPropertyElementMain)
}

func defaultInput() -> AudioDeviceID {
    var addr = address(kAudioHardwarePropertyDefaultInputDevice)
    var id = AudioDeviceID(0)
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &id)
    return id
}

func setDefaultInput(_ id: AudioDeviceID) -> OSStatus {
    var addr = address(kAudioHardwarePropertyDefaultInputDevice)
    var value = id
    return AudioObjectSetPropertyData(system, &addr, 0, nil, UInt32(MemoryLayout<AudioDeviceID>.size), &value)
}

func allDevices() -> [AudioDeviceID] {
    var addr = address(kAudioHardwarePropertyDevices)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(system, &addr, 0, nil, &size) == noErr else { return [] }
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    guard AudioObjectGetPropertyData(system, &addr, 0, nil, &size, &ids) == noErr else { return [] }
    return ids
}

func text(_ id: AudioObjectID, _ selector: AudioObjectPropertySelector) -> String {
    var addr = address(selector)
    var size = UInt32(MemoryLayout<CFString?>.size)
    var value: CFString? = nil
    let status = withUnsafeMutablePointer(to: &value) { AudioObjectGetPropertyData(id, &addr, 0, nil, &size, $0) }
    return status == noErr ? ((value as String?) ?? "") : ""
}

func inputChannels(_ id: AudioDeviceID) -> Int {
    var addr = address(kAudioDevicePropertyStreamConfiguration, kAudioObjectPropertyScopeInput)
    var size: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(id, &addr, 0, nil, &size) == noErr, size > 0 else { return 0 }
    let raw = UnsafeMutableRawPointer.allocate(byteCount: Int(size), alignment: MemoryLayout<AudioBufferList>.alignment)
    defer { raw.deallocate() }
    guard AudioObjectGetPropertyData(id, &addr, 0, nil, &size, raw) == noErr else { return 0 }
    let list = UnsafeMutableAudioBufferListPointer(raw.assumingMemoryBound(to: AudioBufferList.self))
    return list.reduce(0) { $0 + Int($1.mNumberChannels) }
}

func device(uid: String) -> AudioDeviceID? {
    allDevices().first { text($0, kAudioDevicePropertyDeviceUID) == uid }
}

func describe(_ id: AudioDeviceID) -> String {
    "id=\(id) name=\(text(id, kAudioObjectPropertyName)) uid=\(text(id, kAudioDevicePropertyDeviceUID)) inputs=\(inputChannels(id))"
}

func stamp() -> String {
    let format = DateFormatter(); format.dateFormat = "HH:mm:ss.SSS"; return format.string(from: Date())
}

func watchDefault(_ seconds: Double, _ last: inout AudioDeviceID) {
    let deadline = Date().addingTimeInterval(seconds)
    while Date() < deadline {
        let now = defaultInput()
        if now != last { print("\(stamp()) default_now: " + describe(now)); last = now }
        Thread.sleep(forTimeInterval: 0.1)
    }
}

let args = CommandLine.arguments
let mode = args.count > 1 ? args[1] : ""

if mode == "list" {
    print("default_input: " + describe(defaultInput()))
    for id in allDevices() where inputChannels(id) > 0 { print("input: " + describe(id)) }
    if let leftover = device(uid: testUID) { print("leftover_test_aggregate: " + describe(leftover)) }
    exit(0)
}

if mode == "cleanup" {
    if let leftover = device(uid: testUID) {
        print("destroy_test_aggregate status=\(AudioHardwareDestroyAggregateDevice(leftover))")
    } else {
        print("no test aggregate")
    }
    exit(0)
}

let secondsArgument = mode == "switch" ? 3 : 2
let settle = mode == "emptydefault" && args.count == 4 ? Double(args[3]) : 2.5
guard args.count > secondsArgument, let seconds = Double(args[secondsArgument]), seconds > 0, seconds <= 10,
      let settle = settle, settle > 0, settle <= 60,
      (mode == "switch" && args.count == 4) || (mode == "emptydefault" && (args.count == 3 || args.count == 4)) else {
    FileHandle.standardError.write(Data("usage: input_device_fault list | cleanup | switch <uid> <seconds<=10> | emptydefault <seconds<=10> [settle<=60]\n".utf8))
    exit(2)
}

let original = defaultInput()
print("\(stamp()) mode=\(mode) original_default: " + describe(original))
var last = original
var aggregate: AudioDeviceID?
var restored = false
func restore() {
    print("\(stamp()) restore_default status=\(setDefaultInput(original))")
    if let id = aggregate { print("\(stamp()) destroy_aggregate status=\(AudioHardwareDestroyAggregateDevice(id))") }
    restored = true
}
defer { if !restored { restore() } }

if mode == "switch" {
    guard let target = device(uid: args[2]), target != original else { print("refuse: unknown target or already default"); exit(3) }
    print("\(stamp()) set_default status=\(setDefaultInput(target)) target: " + describe(target))
    watchDefault(seconds, &last)
} else {
    guard device(uid: testUID) == nil else { print("refuse: test aggregate exists; run cleanup"); exit(3) }
    let sub = text(original, kAudioDevicePropertyDeviceUID)
    let description: [String: Any] = [
        kAudioAggregateDeviceNameKey: "Jarvis input fault test",
        kAudioAggregateDeviceUIDKey: testUID,
        kAudioAggregateDeviceSubDeviceListKey: [[kAudioSubDeviceUIDKey: sub]],
        kAudioAggregateDeviceMainSubDeviceKey: sub,
        kAudioAggregateDeviceIsPrivateKey: 0,
    ]
    var id = AudioDeviceID(0)
    let created = AudioHardwareCreateAggregateDevice(description as CFDictionary, &id)
    print("\(stamp()) create_aggregate status=\(created) id=\(id)")
    guard created == noErr, id != 0 else { exit(4) }
    aggregate = id
    Thread.sleep(forTimeInterval: 1)
    print("\(stamp()) set_default status=\(setDefaultInput(id)) aggregate: " + describe(id))
    watchDefault(settle, &last)
    var subDevices = address(kAudioAggregateDevicePropertyFullSubDeviceList)
    var empty = [String]() as CFArray
    let emptied = withUnsafePointer(to: &empty) {
        AudioObjectSetPropertyData(id, &subDevices, 0, nil, UInt32(MemoryLayout<CFArray>.size), $0)
    }
    print("\(stamp()) empty_aggregate status=\(emptied)")
    watchDefault(seconds, &last)
}
restore()
Thread.sleep(forTimeInterval: 0.5)
print("\(stamp()) final_default: " + describe(defaultInput()) + " leftover_aggregate=\(device(uid: testUID) != nil)")
