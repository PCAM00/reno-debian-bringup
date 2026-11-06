#!/usr/bin/env python3
"""Fail-closed compile-time invariants for Reno RAM diagnostics."""
import argparse
import json
from pathlib import Path
import re
import struct
import subprocess


def require_config(path):
    text = path.read_text()
    config = dict(re.findall(r"^(CONFIG_[A-Z0-9_]+)=(.*)$", text, re.M))
    required = (
        "ARM64 ARCH_QCOM BLK_DEV_INITRD RD_GZIP BINFMT_ELF BINFMT_SCRIPT "
        "PROC_FS SYSFS TMPFS DEVTMPFS CONFIGFS_FS INET UNIX NETDEVICES "
        "USB_GADGET USB_DWC3 USB_DWC3_QCOM USB_DWC3_GADGET "
        "USB_CONFIGFS USB_CONFIGFS_NCM USB_CONFIGFS_ACM "
        "PHY_QCOM_QUSB2 SPMI_MSM_PMIC_ARB MFD_SPMI_PMIC "
        "REGULATOR_QCOM_RPMH QCOM_RPMH QCOM_COMMAND_DB QCOM_SMEM "
        "QCOM_SOC QCOM_RPMHPD QCOM_AOSS_QMP THERMAL_OF "
        "CPU_FREQ_DEFAULT_GOV_POWERSAVE CPU_FREQ_GOV_POWERSAVE CPU_FREQ_THERMAL "
        "QCOM_TSENS QCOM_SPMI_TEMP_ALARM POWER_RESET_QCOM_PON TTY "
        "SDM_GCC_845 PINCTRL_SDM670 INTERCONNECT_QCOM_SDM670 ARM_SMMU"
    ).split()
    prohibited = (
        "BLOCK MODULES DEVMEM MTD MMC SCSI EFI EFIVAR_FS KEXEC "
        "PSTORE HIBERNATION REMOTEPROC DRM SOUND WLAN BT "
        "CHARGER_QCOM_SMB2 CHARGER_QCOM_SMBB BATTERY_QCOM_FG QCOM_RMTFS_MEM "
        "TYPEC USB_CONFIGFS_MASS_STORAGE CPU_FREQ_DEFAULT_GOV_PERFORMANCE QCOM_LMH"
    ).split()
    errors = [f"CONFIG_{x} must be y (is {config.get('CONFIG_'+x, 'n')})"
              for x in required if config.get("CONFIG_"+x) != "y"]
    errors += [f"CONFIG_{x} must be disabled" for x in prohibited
               if config.get("CONFIG_"+x, "n") != "n"]
    if config.get('CONFIG_LOCALVERSION') != '"-oppo-pcam00-b-ramdiag-v4"':
        errors.append('CONFIG_LOCALVERSION must identify the B v4 kernel')
    if errors:
        raise SystemExit("\n".join(errors))
    return {"required_builtin": required, "prohibited": prohibited,
            "revision": "pcam00-b-ramdiag-v4", "result": "PASS"}


from image_formats import MAX_DTB, parse_fdt, read_regular


def fdt_nodes(path):
    return parse_fdt(read_regular(path, MAX_DTB))


def require_dtb(path):
    return require_board_nodes(fdt_nodes(path))


def require_board_nodes(nodes):
    root=nodes['/']
    expected_compat=b'oppo,pcam00\0qcom,sdm710\0qcom,sdm670\0'
    if root.get('compatible') != expected_compat:
        raise ValueError('Not the exact reviewed Reno compatible list')
    if root.get('oppo,ramdiag-revision') != b'pcam00-b-ramdiag-v4\0':
        raise ValueError('Wrong diagnostic DT revision')
    if root.get('oppo,prjversion') != struct.pack('>II',1,18097):
        raise ValueError('Wrong OPPO project ID')
    if root.get('qcom,board-id') != struct.pack('>II',8,0):
        raise ValueError('Wrong OPPO board ID')
    if root.get('qcom,msm-id') != struct.pack('>4I',360,0,393,0):
        raise ValueError('Wrong OPPO SoC identifier tuples')
    if root.get('qcom,pmic-id') != struct.pack('>12I',0x1001b,0x1011a,0,0,0x1001b,0x2001a,0,0,0x1001b,0x202001a,0,0):
        raise ValueError('Changed PMIC candidate identifiers; these are not proven live exact-match results')
    if 'qcom,pmic-id-size' in root and root['qcom,pmic-id-size'] != struct.pack('>I',4):
        raise ValueError('Unexpected PMIC tuple width')
    for prop in ('#address-cells','#size-cells'):
        if root.get(prop)!=struct.pack('>I',2):
            raise ValueError('Require two-cell root addressing and size')
    # Overlay products and numeric phandles from stock cannot be spliced into
    # this tree. A passing structural validator is not an ABL handoff proof.
    if any(n=='/soc' or n.startswith('/soc/') or n.startswith('/fragment@')
           or n in ('/__fixups__','/__local_fixups__') for n in nodes):
        raise ValueError('Unexpected stock/overlay namespace in board tree')
    phandles={}
    for node, props in nodes.items():
        if 'phandle' in props:
            raw=props['phandle']
            if len(raw)!=4 or raw in (bytes(4),b'\xff'*4) or raw in phandles:
                raise ValueError('Invalid or duplicate phandle')
            phandles[raw]=node
        if 'linux,phandle' in props and props.get('phandle',props['linux,phandle'])!=props['linux,phandle']:
            raise ValueError('Conflicting phandle encodings')
    memory_nodes = [n for n,p in nodes.items() if p.get('device_type') == b'memory\0']
    if memory_nodes != ['/memory'] or nodes['/memory'].get('reg') != bytes(16):
        raise ValueError('Use original OPPO /memory zero-length placeholder; do not guess RAM')
    # stock reserved-memory union, checked for coverage and overlaps.
    expected=[(0x85700000,0x600000),(0x85e00000,0x100000),
              (0x85fc0000,0x4940000),(0x8ab00000,0x500000),
              (0x8b000000,0x9600000),(0x94600000,0x500000),
              (0x94b00000,0x100000),(0x94c00000,0x1e00000),
              (0x96a00000,0x200000),(0x96c00000,0x1e00000),
              (0x98a00000,0x10000),(0x98a10000,0x5000),
              (0x98a15000,0x2000),(0x9c000000,0x2300000),
              (0x9e300000,0x100000),(0x9e400000,0x1400000),
              (0xa7800000,0xc00000),(0xb7e00000,0x400000),
              (0xb8200000,0x100000)]
    spans=[]
    for node,props in nodes.items():
        if not node.startswith('/reserved-memory/') or node.count('/') != 2:
            continue
        reg=props.get('reg',b'')
        if not reg or len(reg)%16 or 'no-map' not in props:
            raise ValueError(f'Expected fixed two-cell no-map reservation: {node}')
        for off in range(0,len(reg),16):
            address,length=struct.unpack_from('>QQ',reg,off)
            if not length or address+length>1<<64:
                raise ValueError(f'Invalid reserved range: {node}')
            spans.append((address,address+length,node))
    spans.sort()
    for a,b in zip(spans,spans[1:]):
        if a[1]>b[0]:
            raise ValueError(f'Reserved memory overlap: {a} / {b}')
    for start,size in expected:
        cursor=start
        for low,high,_ in spans:
            if low<=cursor<high:
                cursor=high
        if cursor<start+size:
            raise ValueError(f'Missing stock reserved span {start:#x}+{size:#x}')
    disabled=('ufshc@1d84000','phy@1d87000','mmc@7c4000','mmc@8804000',
              'remoteproc@4080000','remoteproc@8300000','remoteproc@62400000',
              'gpu@5000000','gmu@506a000','display-subsystem@ae00000',
              'charger@1000','typec@1300')
    for suffix in disabled:
        matches=[p for n,p in nodes.items() if n.endswith('/'+suffix)]
        if len(matches)!=1 or matches[0].get('status')!=b'disabled\0':
            raise ValueError(f'Expected disabled node: {suffix}')
    prohibited_fragments=(b'ufshc',b'qmp-ufs',b'sdhci',b'arm,pl18',b'remoteproc',
        b'charger',b'qpnp-smb',b'fastcg',b'vooc',b'bq25882',b'da9313',b'rmtfs')
    for node, props in nodes.items():
        compat=props.get('compatible',b'')
        if any(value in compat for value in prohibited_fragments):
            if props.get('status')!=b'disabled\0':
                raise ValueError(f'Unexpected enabled prohibited device node: {node}')
    usb = nodes['/soc@0/usb@a6f8800/usb@a600000']
    if usb.get('phy-names') != b'usb2-phy\0' or len(usb.get('phys', b'')) != 4:
        raise ValueError('Diagnostic USB must use exactly one USB2 PHY')
    phy = [p for p in nodes.values() if p.get('phandle') == usb['phys']]
    if len(phy) != 1 or phy[0].get('status') != b'okay\0':
        raise ValueError('USB PHY is not enabled')
    if usb.get('dr_mode') != b'peripheral\0' or usb.get('maximum-speed') != b'high-speed\0':
        raise ValueError('Diagnostic USB must be USB2 device-only')
    tuning = {'qcom,imp-res-offset-value': 8, 'qcom,bias-ctrl-value': 32,
              'qcom,charge-ctrl-value': 0, 'qcom,hstx-trim-value': 7,
              'qcom,preemphasis-level': 3, 'qcom,preemphasis-width': 1,
              'qcom,hsdisc-trim-value': 1}
    for prop, value in tuning.items():
        if phy[0].get(prop) != struct.pack('>I', value):
            raise ValueError(f'Wrong OPPO USB PHY tuning: {prop}')
    if any(key in phy[0] for key in ('nvmem-cells', 'nvmem-cell-names')):
        raise ValueError('nvmem would overwrite OPPO fixed HSTX after DT override')
    for prop, (lo,hi) in {
        'vdd-supply': (880000,900000),
        'vdda-pll-supply': (1800000,1800000),
        'vdda-phy-dpdm-supply': (3088000,3100000)
    }.items():
        supply=phy[0].get(prop,b'')
        if len(supply)!=4 or supply not in phandles:
            raise ValueError(f'Unresolved USB supply: {prop}')
        regulator=nodes[phandles[supply]]
        if regulator.get('regulator-min-microvolt')!=struct.pack('>I',lo) or regulator.get('regulator-max-microvolt')!=struct.pack('>I',hi):
            raise ValueError(f'Wrong voltage bounds on USB supply: {prop}')
    ufs = [p for n,p in nodes.items() if n.endswith('/ufshc@1d84000')][0]
    if ufs.get('lanes-per-direction') != struct.pack('>I', 1):
        raise ValueError('Reno UFS is single lane; still must stay disabled')
    return {'model':root['model'].rstrip(b'\0').decode(),
            'revision': 'pcam00-b-ramdiag-v4',
            'abl_exact_match_and_dtbo_skip': 'NOT_PROVEN_ON_TARGET',
            'official_source_audit': '61711727601fe83c11947f308e9987aaf2217652',
            'usb_tuning': tuning, 'usb_tune1_composed': '0x77',
            'memory_placeholder_path': '/memory', 'ufs_lanes_disabled': 1,
            'usb2_phy_only': True,
            'reserved_spans':[{'start':hex(a),'end':hex(b),'node':n} for a,b,n in spans],
            'stock_reservations_covered':True,'disabled_nodes':list(disabled),
            'result':'PASS','warning':'Structural checks only, not hardware validation'}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--config',type=Path,required=True)
    ap.add_argument('--dtb',type=Path)
    ap.add_argument('--output',type=Path)
    a=ap.parse_args()
    report={'config':require_config(a.config)}
    if a.dtb:report['device_tree']=require_dtb(a.dtb)
    text=json.dumps(report,indent=2)+'\n'
    if a.output:
        with a.output.open('x') as f:f.write(text)
    print(text)


if __name__=='__main__':main()
