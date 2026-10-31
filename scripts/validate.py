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
        "ARCH_QCOM QCOM_SOC QCOM_RPMHPD QCOM_AOSS_QMP THERMAL_OF "
        "SDM_GCC_845 PINCTRL_SDM670 INTERCONNECT_QCOM_SDM670 ARM_SMMU"
    ).split()
    prohibited = (
        "BLOCK MODULES DEVMEM MTD MMC SCSI EFI EFIVAR_FS KEXEC "
        "PSTORE HIBERNATION REMOTEPROC DRM SOUND WLAN BT "
        "CHARGER_QCOM_SMB2 CHARGER_QCOM_SMBB BATTERY_QCOM_FG QCOM_RMTFS_MEM "
        "TYPEC USB_CONFIGFS_MASS_STORAGE"
    ).split()
    errors = [f"CONFIG_{x} must be y (is {config.get('CONFIG_'+x, 'n')})"
              for x in required if config.get("CONFIG_"+x) != "y"]
    errors += [f"CONFIG_{x} must be disabled" for x in prohibited
               if config.get("CONFIG_"+x, "n") != "n"]
    if errors:
        raise SystemExit("\n".join(errors))
    return {"required_builtin": required, "prohibited": prohibited, "result": "PASS"}


def fdt_nodes(path):
    b = path.read_bytes()
    if len(b) < 40:
        raise ValueError("Truncated FDT")
    magic,total,off_struct,off_strings,_,version,_,_,strsz,structsz = struct.unpack_from(">10I", b)
    if magic != 0xd00dfeed or total != len(b) or version < 17:
        raise ValueError("Invalid FDT header")
    strings = b[off_strings:off_strings+strsz]
    pos,end = off_struct,off_struct+structsz
    stack=[]; nodes={}
    while pos < end:
        tag,=struct.unpack_from(">I",b,pos);pos+=4
        if tag == 1:
            stop=b.index(0,pos,end);name=b[pos:stop].decode();pos=(stop+4)&~3
            stack.append(name);nodes.setdefault('/'+'/'.join(stack[1:]),{})
        elif tag == 2:
            stack.pop()
        elif tag == 3:
            size,noff=struct.unpack_from('>II',b,pos);pos+=8
            name=strings[noff:strings.index(0,noff)].decode()
            nodes['/'+'/'.join(stack[1:])][name]=b[pos:pos+size];pos=(pos+size+3)&~3
        elif tag == 4:
            continue
        elif tag == 9:
            break
        else:
            raise ValueError(f"Unexpected FDT tag {tag}")
    return nodes


def require_dtb(path):
    nodes=fdt_nodes(path)
    root=nodes['/']
    if b'oppo,pcam00\0' not in root.get('compatible',b''):
        raise ValueError('Not a Reno DTB')
    if root.get('oppo,prjversion') != struct.pack('>II',1,18097):
        raise ValueError('Wrong OPPO project ID')
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
        for off in range(0,len(reg),16):
            address,length=struct.unpack_from('>QQ',reg,off)
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
    return {'model':root['model'].rstrip(b'\0').decode(),
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
