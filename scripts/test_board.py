#!/usr/bin/env python3
"""Negative controls for the OPPO-source revision; no hardware operations."""
import copy
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest

from validate import fdt_nodes, require_board_nodes, require_config


U32 = lambda n: struct.pack('>I', n)


def minimum_board():
    # Deliberately synthetic fixtures test policy, not real DTB interoperability.
    spans = [(0x85700000,0x600000),(0x85e00000,0x100000),(0x85fc0000,0x4940000),
             (0x8ab00000,0x500000),(0x8b000000,0x9600000),(0x94600000,0x500000),
             (0x94b00000,0x100000),(0x94c00000,0x1e00000),(0x96a00000,0x200000),
             (0x96c00000,0x1e00000),(0x98a00000,0x10000),(0x98a10000,0x5000),
             (0x98a15000,0x2000),(0x9c000000,0x2300000),(0x9e300000,0x100000),
             (0x9e400000,0x1400000),(0xa7800000,0xc00000),
             (0xb7e00000,0x400000),(0xb8200000,0x100000)]
    nodes={'/':{'compatible':b'oppo,pcam00\0','model':b'Fixture\0',
                'oppo,prjversion':U32(1)+U32(18097),'qcom,board-id':U32(8)+U32(0)},
           '/memory':{'device_type':b'memory\0','reg':bytes(16)}}
    for addr,size in spans:
        nodes[f'/reserved-memory/test@{addr:x}']={'reg':struct.pack('>QQ',addr,size)}
    for name in ['ufshc@1d84000','phy@1d87000','mmc@7c4000','mmc@8804000',
                 'remoteproc@4080000','remoteproc@8300000','remoteproc@62400000',
                 'gpu@5000000','gmu@506a000','display-subsystem@ae00000',
                 'charger@1000','typec@1300']:
        nodes['/soc@0/'+name]={'status':b'disabled\0'}
    nodes['/soc@0/ufshc@1d84000']['lanes-per-direction']=U32(1)
    nodes['/soc@0/phy@88e2000']={'phandle':U32(9),'status':b'okay\0',
        **{key:U32(value) for key,value in {
            'qcom,imp-res-offset-value':8,'qcom,bias-ctrl-value':32,
            'qcom,charge-ctrl-value':0,'qcom,hstx-trim-value':7,
            'qcom,preemphasis-level':3,'qcom,preemphasis-width':1,
            'qcom,hsdisc-trim-value':1}.items()}}
    nodes['/soc@0/usb@a6f8800/usb@a600000']={
        'phys':U32(9),'phy-names':b'usb2-phy\0','dr_mode':b'peripheral\0',
        'maximum-speed':b'high-speed\0'}
    return nodes


class BoardTests(unittest.TestCase):
    def setUp(self):
        path=os.environ.get('TEST_DTB')
        self.nodes=fdt_nodes(Path(path)) if path else minimum_board()

    def test_positive(self):
        self.assertEqual(require_board_nodes(self.nodes)['result'],'PASS')

    def test_tune1_mapping(self):
        self.assertEqual((7 << 4) | (1 << 2) | 3,0x77)

    def test_regressions_rejected(self):
        phy='/soc@0/phy@88e2000'
        ufs='/soc@0/ufshc@1d84000'
        changes=[(phy,'qcom,hstx-trim-value',U32(3)),
                 (phy,'qcom,imp-res-offset-value',U32(0)),
                 (phy,'nvmem-cells',U32(1)),
                 (ufs,'status',b'okay\0'),(ufs,'lanes-per-direction',U32(2)),
                 ('/memory','reg',struct.pack('>QQ',0x80000000,0x100000000)),
                 ('/','oppo,prjversion',U32(1)+U32(18081))]
        for node,prop,value in changes:
            with self.subTest(node=node,prop=prop):
                bad=copy.deepcopy(self.nodes);bad[node][prop]=value
                with self.assertRaises(ValueError):require_board_nodes(bad)

    def test_renamed_memory_rejected(self):
        bad=copy.deepcopy(self.nodes);bad['/memory@80000000']=bad.pop('/memory')
        with self.assertRaises(ValueError):require_board_nodes(bad)

    def test_config_regressions(self):
        path=os.environ.get('TEST_CONFIG')
        if not path:self.skipTest('resolved config is tested after CI build')
        config=Path(path).read_text()
        self.assertEqual(require_config(Path(path))['result'],'PASS')
        with tempfile.TemporaryDirectory() as temp:
            for extra in ['CONFIG_BLOCK=y','CONFIG_QCOM_LMH=y','CONFIG_CPU_FREQ_DEFAULT_GOV_PERFORMANCE=y']:
                p=Path(temp)/'bad.config';p.write_text(config+'\n'+extra+'\n')
                with self.subTest(extra=extra), self.assertRaises(SystemExit):require_config(p)


if __name__=='__main__':unittest.main()
