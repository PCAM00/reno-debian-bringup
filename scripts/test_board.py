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
    nodes={'/':{'compatible':b'oppo,pcam00\0qcom,sdm710\0qcom,sdm670\0','model':b'Fixture\0',
                '#address-cells':U32(2),'#size-cells':U32(2),
                'oppo,ramdiag-revision':b'pcam00-b-ramdiag-v4\0',
                'qcom,msm-id':struct.pack('>4I',0x30168,0,0x30189,0),
                'oppo,ramdiag-board-variant':b'B-F3\0',
                'qcom,pmic-id':struct.pack('>12I',0x1001b,0x1011a,0,0,0x1001b,0x2001a,0,0,0x1001b,0x202001a,0,0),
                'oppo,prjversion':U32(1)+U32(18097),'qcom,board-id':U32(8)+U32(0)},
           '/memory':{'device_type':b'memory\0','reg':bytes(16)}}
    for addr,size in spans:
        nodes[f'/reserved-memory/test@{addr:x}']={'reg':struct.pack('>QQ',addr,size),'no-map':b''}
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
    for handle,prop,lo,hi in [
            (10,'vdd-supply',880000,900000),
            (11,'vdda-pll-supply',1800000,1800000),
            (12,'vdda-phy-dpdm-supply',3088000,3100000)]:
        nodes[f'/soc@0/regulator@{handle}']={'phandle':U32(handle),
            'regulator-min-microvolt':U32(lo),'regulator-max-microvolt':U32(hi)}
        nodes['/soc@0/phy@88e2000'][prop]=U32(handle)
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

    def test_foundry3_encoding_and_old_candidate_rejection(self):
        report=require_board_nodes(self.nodes)
        self.assertEqual(report['board_variant'],'B-F3')
        self.assertEqual(report['foundry_fields'],[3,3])
        cells=struct.unpack('>4I',self.nodes['/']['qcom,msm-id'])
        self.assertEqual([cells[i]&0xffff for i in (0,2)],[360,393])
        for values in ((360,0,393,0),(0x30168,0,393,0),(360,0,0x30189,0),
                       (0x20168,0,0x20189,0),(0x30168,1,0x30189,0)):
            bad=copy.deepcopy(self.nodes)
            bad['/']['qcom,msm-id']=struct.pack('>4I',*values)
            with self.subTest(values=values),self.assertRaises(ValueError):require_board_nodes(bad)
        for marker in (None,b'B-F0\0',b'B-F3'):
            bad=copy.deepcopy(self.nodes)
            if marker is None:bad['/'].pop('oppo,ramdiag-board-variant')
            else:bad['/']['oppo,ramdiag-board-variant']=marker
            with self.subTest(marker=marker),self.assertRaises(ValueError):require_board_nodes(bad)

    def test_regressions_rejected(self):
        phy='/soc@0/phy@88e2000'
        ufs='/soc@0/ufshc@1d84000'
        changes=[(phy,'qcom,hstx-trim-value',U32(3)),
                 (phy,'qcom,imp-res-offset-value',U32(0)),
                 (phy,'nvmem-cells',U32(1)),
                 (ufs,'status',b'okay\0'),(ufs,'lanes-per-direction',U32(2)),
                 ('/memory','reg',struct.pack('>QQ',0x80000000,0x100000000)),
                 ('/','oppo,prjversion',U32(1)+U32(18081)),
                 ('/','qcom,msm-id',struct.pack('>4I',864,0,393,0)),
                 ('/','qcom,pmic-id',U32(0)),
                 ('/','qcom,pmic-id-size',U32(3)),
                 ('/','oppo,ramdiag-revision',b'old\0')]
        for node,prop,value in changes:
            with self.subTest(node=node,prop=prop):
                bad=copy.deepcopy(self.nodes);bad[node][prop]=value
                with self.assertRaises(ValueError):require_board_nodes(bad)

    def test_renamed_memory_rejected(self):
        bad=copy.deepcopy(self.nodes);bad['/memory@80000000']=bad.pop('/memory')
        with self.assertRaises(ValueError):require_board_nodes(bad)

    def test_extra_nodes_and_bad_supply_rejected(self):
        for path,props in [
            ('/soc',{}),
            ('/soc@0/extra-charger',{'compatible':b'qcom,qpnp-smb2\0'}),
            ('/memory@other',{'device_type':b'memory\0','reg':bytes(16)}),
            ('/soc@0/duplicate-handle',{'phandle':U32(9)}),
            ('/fragment@0',{'target':U32(9)})]:
            bad=copy.deepcopy(self.nodes);bad[path]=props
            with self.subTest(path=path),self.assertRaises(ValueError):require_board_nodes(bad)
        bad=copy.deepcopy(self.nodes)
        phy=bad['/soc@0/phy@88e2000']
        phy['vdd-supply']=U32(0xffffffff)
        with self.assertRaises(ValueError):require_board_nodes(bad)
        bad=copy.deepcopy(self.nodes)
        first=next(n for n in bad if n.startswith('/reserved-memory/'))
        bad[first].pop('no-map')
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
