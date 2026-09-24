"""Read/set built-in Mac display brightness using the installed system framework."""
import argparse
import ctypes
import json
import time


def brightness(level=None):
    if level is not None and (isinstance(level,bool) or not isinstance(level,int) or not 0 <= level <= 16):
        raise ValueError('invalid_level')
    cg=ctypes.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
    ds=ctypes.CDLL('/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices')
    ids=(ctypes.c_uint32*16)();count=ctypes.c_uint32()
    cg.CGGetOnlineDisplayList.argtypes=[ctypes.c_uint32,ctypes.POINTER(ctypes.c_uint32),ctypes.POINTER(ctypes.c_uint32)]
    cg.CGDisplayIsBuiltin.argtypes=[ctypes.c_uint32];cg.CGDisplayIsBuiltin.restype=ctypes.c_uint32
    if cg.CGGetOnlineDisplayList(16,ids,ctypes.byref(count)) != 0:raise RuntimeError('display_list_failed')
    ds.DisplayServicesGetBrightness.argtypes=[ctypes.c_uint32,ctypes.POINTER(ctypes.c_float)]
    ds.DisplayServicesSetBrightness.argtypes=[ctypes.c_uint32,ctypes.c_float]
    results=[]
    for display in ids[:count.value]:
        if not cg.CGDisplayIsBuiltin(display):continue
        before=ctypes.c_float()
        if ds.DisplayServicesGetBrightness(display,ctypes.byref(before)) != 0:raise RuntimeError('brightness_read_failed')
        if level is not None:
            if ds.DisplayServicesSetBrightness(display,ctypes.c_float(level/16)) != 0:raise RuntimeError('brightness_write_failed')
            time.sleep(.15)
        after=ctypes.c_float()
        if ds.DisplayServicesGetBrightness(display,ctypes.byref(after)) != 0:raise RuntimeError('brightness_read_failed')
        results.append({'before':before.value,'observed':after.value})
    if not results:raise RuntimeError('builtin_display_unavailable')
    verified=level is None or all(abs(x['observed']-level/16)<.015 for x in results)
    return {'status':'ok' if verified else 'unverified','level':level,'displays':results,'verified':verified}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--level',type=int,choices=range(17));args=parser.parse_args()
    try:
        result=brightness(args.level);print(json.dumps(result));raise SystemExit(0 if result['verified'] else 1)
    except (OSError,ValueError,RuntimeError,AttributeError):
        print(json.dumps({'status':'error','verified':False}));raise SystemExit(1)
