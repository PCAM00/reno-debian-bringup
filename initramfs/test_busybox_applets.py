#!/usr/bin/env python3
"""Inert tests of the exact packaged ARM64 BusyBox via QEMU on CI only.

Never run /init/USB helpers, real mount, reboot or terminating signals. Only
shell builtins, timeout(true), and signal 0 against this process's own group.
"""
from pathlib import Path
import argparse
import json
import os
import subprocess


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--busybox',type=Path,required=True)
    ap.add_argument('--qemu',default='qemu-aarch64')
    ap.add_argument('--output',type=Path,required=True)
    a=ap.parse_args()
    busy=str(a.busybox.resolve(strict=True))
    tests={
        'ash-signal-zero-process-group': 'kill -0 "-$1"',
        'local-read-return-builtins': 'f(){ local x; IFS= read -r x; [ "$x" = fixture ]; }; printf "fixture\\n" | f',
        'single-signal-handler-idempotence': 'stopping=0; calls=0; stop(){ [ "$stopping" = 0 ] || return 0; stopping=1; calls=$((calls+1)); }; stop; stop; [ "$calls" = 1 ]',
    }
    results=[]
    pgid=os.getpgrp()
    for name,code in tests.items():
        r=subprocess.run([a.qemu,busy,'sh','-c',code,'fixture',str(pgid)],
                         capture_output=True,text=True,timeout=10)
        if r.returncode:
            raise SystemExit(f'{name}: FAIL rc={r.returncode}: {r.stderr}')
        results.append({'test':name,'exit_code':r.returncode})
    # Explicit signal makes the negative process-group number unambiguous.
    r=subprocess.run([a.qemu,busy,'kill','-0','-'+str(pgid)],capture_output=True,text=True,timeout=10)
    if r.returncode:raise SystemExit('BusyBox kill negative-pgid signal-0 syntax failed: '+r.stderr)
    results.append({'test':'kill-applet-negative-pgid-signal-zero','exit_code':0})
    # timeout only wraps another QEMU process executing true; never a device action.
    r=subprocess.run([a.qemu,busy,'timeout','-s','KILL','3',a.qemu,busy,'true'],
                     capture_output=True,text=True,timeout=10)
    if r.returncode:raise SystemExit('BusyBox timeout option semantics failed: '+r.stderr)
    results.append({'test':'timeout-kill-option-with-true','exit_code':0})
    record={'scope':'inert QEMU-user applet semantics, not target hardware/boot validation',
            'target_init_executed':False,'mount_or_reboot_executed':False,
            'terminating_signals_sent':False,'result':'PASS','tests':results}
    with a.output.open('x',encoding='utf-8') as f:json.dump(record,f,indent=2);f.write('\n')
    print(json.dumps(record,indent=2))

if __name__=='__main__':
    main()
