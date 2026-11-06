#!/usr/bin/env python3
"""Synthetic host tests. NEVER execute or source the target /init or usb.sh."""
import gzip
import importlib.util
import io
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('mkcpio',HERE/'mkcpio.py')
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def parse(data):
    result={};pos=0
    while True:
        assert data[pos:pos+6]==b'070701'
        fields=[int(data[pos+6+n*8:pos+14+n*8],16) for n in range(13)]
        pos+=110
        name=data[pos:pos+fields[11]-1].decode()
        pos=(pos+fields[11]+3)&~3
        payload=data[pos:pos+fields[6]];pos=(pos+fields[6]+3)&~3
        result[name]=(fields,payload)
        if name=='TRAILER!!!':return result


def host(script, *args):
    # No target filesystem/command execution. Only source-only helpers and mocks.
    return subprocess.run(['sh','-c',script,'host-fixture',*map(str,args)],
                          capture_output=True,text=True,timeout=8)


def zone(root,index,value,type_name='cpu-fixture'):
    d=root/f'thermal_zone{index}';d.mkdir(exist_ok=True)
    if value is not None:(d/'temp').write_text(value+'\n')
    if type_name is not None:(d/'type').write_text(type_name+'\n')
    return d


class Tests(unittest.TestCase):
    def test_shell_syntax_only(self):
        subprocess.run(['bash','-n',str(HERE/'build.sh')],check=True)
        for name in ('init','safety.sh','runtime.sh','usb.sh'):
            subprocess.run(['sh','-n',str(HERE/name)],check=True)

    def test_newc(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'dev').mkdir();(root/'bin').mkdir()
            (root/'bin/busybox').write_bytes(b'fixture')
            has_symlinks=True
            try:(root/'bin/sh').symlink_to('busybox')
            except OSError:has_symlinks=False
            (root/'init').write_bytes(b'#!/bin/sh\n');(root/'init').chmod(0o755)
            stream=io.BytesIO();mod.archive(root,42,stream)
            entries=parse(gzip.decompress(gzip.compress(stream.getvalue(),mtime=0)))
            f,_=entries['dev/console']
            self.assertEqual(f[1],stat.S_IFCHR|0o600)
            self.assertEqual(f[2:4],[0,0]);self.assertEqual(f[9:11],[5,1])
            f,_=entries['dev/null'];self.assertEqual(f[9:11],[1,3])
            if has_symlinks:self.assertEqual(entries['bin/sh'][1],b'busybox')
            self.assertEqual(entries['init'][0][1],stat.S_IFREG|0o755)
            self.assertTrue(all(v[0][5]==42 for v in entries.values()))
            second=io.BytesIO();mod.archive(root,42,second)
            self.assertEqual(stream.getvalue(),second.getvalue())

    def thermal(self, root, out, baseline=''):
        return host('. "$1"; thermal_snapshot "$2" "$3" "$4"',HERE/'safety.sh',root,out,baseline)

    def test_single_thermal_cases(self):
        cases=[('35000',0),('69999',0),('70000',1),('080000',1),('035000',0),
               ('000000',2),('0',2),('150001',2),('999999999999999',2),
               ('broken',2),('-2147483648',2),('',2),(None,2)]
        for value,expected in cases:
            with self.subTest(value=value),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);zone(root,0,value)
                result=self.thermal(root,root/'snapshot')
                self.assertEqual(result.returncode,expected,result.stderr)
                self.assertIn('COUNTS', (root/'snapshot').read_text())

    def test_no_zone_or_invalid_type_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'snapshot'
            self.assertEqual(self.thermal(root,out).returncode,2)
            zone(root,0,'35000',None)
            self.assertEqual(self.thermal(root,out).returncode,2)
            (root/'thermal_zone0/type').write_text('untrusted type\n')
            self.assertEqual(self.thermal(root,out).returncode,2)

    def test_partial_thermal_failure_is_not_hidden(self):
        for other,expected in [('broken',2),('150001',2),(None,2),('70000',1),('40000',0)]:
            with self.subTest(other=other),tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);zone(root,0,'35000');zone(root,1,other)
                self.assertEqual(self.thermal(root,root/'snapshot').returncode,expected)

    def test_baseline_disappearance_type_change_and_new_invalid_zone(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'snapshot';base=root/'baseline'
            zone(root,0,'35000','soc');zone(root,1,'36000','pmic')
            self.assertEqual(self.thermal(root,out).returncode,0)
            learned=host('. "$1"; thermal_learn_baseline "$2" "$3"',HERE/'safety.sh',out,base)
            self.assertEqual(learned.returncode,0,learned.stderr)
            self.assertEqual(base.read_text(),'thermal_zone0 soc\nthermal_zone1 pmic\n')
            # Fixtures are temporary and no real sysfs is consulted.
            (root/'thermal_zone1/type').write_text('changed\n')
            self.assertEqual(self.thermal(root,out,base).returncode,2)
            (root/'thermal_zone1/type').write_text('pmic\n')
            self.assertEqual(self.thermal(root,out,base).returncode,0)
            os.rename(root/'thermal_zone1',root/'absent-zone1')
            self.assertEqual(self.thermal(root,out,base).returncode,2)
            os.rename(root/'absent-zone1',root/'thermal_zone1')
            zone(root,2,'bad','new')
            self.assertEqual(self.thermal(root,out,base).returncode,2)

    def test_cpu_policy_is_observed_not_programmed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'cpu'
            script='. "$1"; cpu_policy_snapshot "$2" "$3"'
            self.assertEqual(host(script,HERE/'safety.sh',root,out).returncode,2)
            p=root/'policy0';p.mkdir();(p/'scaling_governor').write_text('powersave\n')
            self.assertEqual(host(script,HERE/'safety.sh',root,out).returncode,0)
            (p/'scaling_governor').write_text('performance\n')
            self.assertEqual(host(script,HERE/'safety.sh',root,out).returncode,1)
            self.assertEqual((p/'scaling_governor').read_text(),'performance\n')

    def test_stop_coordinator_order_idempotence_and_reboot_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'uptime').write_text('100.50 99.0\n')
            script='''
. "$1"
DIAG_RUN="$2"; DIAG_UPTIME_FILE="$2/uptime"; DIAG_REVISION=fixture
DIAG_STOPPING=0; DIAG_SEQ=0
# Mock hooks: never kill, unbind or reboot the host.
diag_terminate_children() { printf 'terminate\n' >> "$DIAG_RUN/hooks"; return 1; }
diag_unbind_usb() { printf 'unbind\n' >> "$DIAG_RUN/hooks"; return 1; }
diag_request_reboot() { printf 'reboot\n' >> "$DIAG_RUN/hooks"; return 7; }
diag_stop_once thermal-threshold
diag_stop_once second-stop
printf 'state=%s\n' "$DIAG_STOPPING"
'''
            result=host(script,HERE/'runtime.sh',root)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('state=1',result.stdout)
            self.assertEqual((root/'hooks').read_text(),'terminate\nunbind\nreboot\n')
            stages=(root/'stages.log').read_text()
            self.assertIn('stop-attempt-incomplete',stages)
            self.assertIn('reboot-returned-7-no-services-restarted',stages)
            self.assertEqual((root/'stop-requested').read_text(),'thermal-threshold\n')

    def test_signal_latch_and_cleanup_ignore_repeat_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'uptime').write_text('100.0 1.0\n')
            script='''
. "$1"; DIAG_RUN="$2"; DIAG_UPTIME_FILE="$2/uptime"; DIAG_REVISION=fixture
DIAG_STOPPING=0; DIAG_SEQ=0; DIAG_STOP_REQUEST=
trap 'DIAG_STOP_REQUEST=received-stop-signal' INT TERM USR1
# Only the test shell receives these caught signals. All cleanup hooks are mocks.
kill -USR1 "$$"
[ "$DIAG_STOP_REQUEST" = received-stop-signal ] || exit 11
trap '' INT TERM USR1
diag_terminate_children() {
    printf 'terminate\n' >> "$DIAG_RUN/hooks"
    kill -USR1 "$$"
    return 0
}
diag_unbind_usb() {
    printf 'unbind\n' >> "$DIAG_RUN/hooks"
    kill -TERM "$$"
    return 0
}
diag_request_reboot() { printf 'reboot-mock\n' >> "$DIAG_RUN/hooks"; return 5; }
diag_stop_once "$DIAG_STOP_REQUEST"
[ "$DIAG_STOPPING" = 1 ]
'''
            result=host(script,HERE/'runtime.sh',root)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual((root/'hooks').read_text(),'terminate\nunbind\nreboot-mock\n')
            self.assertIn('reboot-returned-5', (root/'stages.log').read_text())

    def test_deadline_not_reset_by_stage_changes(self):
        for now,start,expected in [('100','100',1),('279','100',1),('280','100',0),('99','100',0),('oops','100',0)]:
            result=host('. "$1"; diag_deadline_reached "$2" "$3" 180',HERE/'runtime.sh',now,start)
            self.assertEqual(result.returncode,expected)

    def test_state_log_bound_and_sequence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'uptime').write_text('9.0 1.0\n')
            script='''
. "$1"; DIAG_RUN="$2"; DIAG_UPTIME_FILE="$2/uptime"; DIAG_REVISION=fixture; DIAG_SEQ=0
i=0; while [ "$i" -lt 300 ]; do diag_stage test-event || exit 1; i=$((i+1)); done
'''
            result=host(script,HERE/'runtime.sh',root)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(len((root/'stages.log').read_text().splitlines()),256)
            self.assertIn('sequence=300', (root/'state.txt').read_text())

    def test_ram_only_target_surface_static(self):
        text=(HERE/'init').read_text();usb=(HERE/'usb.sh').read_text()
        self.assertIn('[ "$$" -eq 1 ]',text)
        for line in text.splitlines():
            if line.startswith('mount -t '):
                self.assertIn(line.split()[2],{'proc','sysfs','devtmpfs','tmpfs','configfs'})
        for command in ('mke2fs','mkfs.','fsck','resize2fs','/dev/block','getty ','telnetd ','sshd ','nc -'):
            self.assertNotIn(command,text+usb)
        self.assertIn('-u 65534:65534',text)
        self.assertIn('remount,ro,nosuid,nodev,noexec tmpfs /www',text)
        self.assertIn('remount,bind,ro,nosuid,nodev,noexec /www/live',text)
        self.assertIn('setsid httpd',text)
        self.assertIn('kill -KILL "-$pid"',text)
        self.assertNotIn('kill -0 --',text)
        self.assertIn("trap '' INT TERM USR1",text)
        self.assertNotIn('( sleep "$seconds"',text)
        self.assertIn('check_readings',text)
        self.assertIn('requesting-ordinary-reboot-not-rollback',(HERE/'runtime.sh').read_text())
        self.assertIn('echo 100 > "$g/configs/c.1/MaxPower"',usb)
        self.assertNotIn('mass_storage',usb)
        prepare=usb.split('usb_prepare() {',1)[1].split('\n}',1)[0]
        self.assertNotIn('set -e',prepare)
        for line in prepare.splitlines():
            if line.strip().startswith(('mkdir ','echo ','ln ')):
                self.assertTrue(line.endswith('|| return 1'),line)

if __name__=='__main__':
    unittest.main(verbosity=2)
