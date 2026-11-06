#!/bin/sh
# Source-only USB helpers. Only target PID1 calls these functions.
# Gadget configfs is RAM state, not a firmware/partition write. No host storage.
usb_prepare() {
    local g="$DIAG_GADGET"
    mkdir -p "$g" || return 1
    echo 0x1d6b > "$g/idVendor" || return 1
    echo 0x0104 > "$g/idProduct" || return 1
    echo 0x0200 > "$g/bcdUSB" || return 1
    echo 0x0100 > "$g/bcdDevice" || return 1
    echo 0xef > "$g/bDeviceClass" || return 1
    echo 0x02 > "$g/bDeviceSubClass" || return 1
    echo 0x01 > "$g/bDeviceProtocol" || return 1
    mkdir -p "$g/strings/0x409" "$g/configs/c.1/strings/0x409" || return 1
    echo reno-b-ramdiag-v4 > "$g/strings/0x409/serialnumber" || return 1
    echo Experimental > "$g/strings/0x409/manufacturer" || return 1
    echo 'PCAM00 RAM diagnostics v4' > "$g/strings/0x409/product" || return 1
    echo 'NCM + ACM enumeration (no shell)' > "$g/configs/c.1/strings/0x409/configuration" || return 1
    # Descriptor metadata only, NOT actual PMIC current limiting.
    echo 100 > "$g/configs/c.1/MaxPower" || return 1
    mkdir -p "$g/functions/ncm.usb0" "$g/functions/acm.usb0" || return 1
    echo 02:00:00:07:00:02 > "$g/functions/ncm.usb0/dev_addr" || return 1
    echo 02:00:00:07:00:01 > "$g/functions/ncm.usb0/host_addr" || return 1
    ln -s "$g/functions/ncm.usb0" "$g/configs/c.1/ncm.usb0" || return 1
    ln -s "$g/functions/acm.usb0" "$g/configs/c.1/acm.usb0" || return 1
}

usb_find_udc() {
    local candidate
    DIAG_UDC=
    for candidate in /sys/class/udc/*; do
        [ -e "$candidate" ] || continue
        # Refuse ambiguity: only one controller should be enabled in this DT.
        [ -z "$DIAG_UDC" ] || return 2
        DIAG_UDC=${candidate##*/}
    done
    [ -n "$DIAG_UDC" ]
}

usb_bind() {
    [ "${DIAG_STOPPING:-0}" = 0 ] || return 1
    [ ! -e "$DIAG_RUN/stop-requested" ] || return 1
    [ -n "$DIAG_UDC" ] || return 1
    printf '%s\n' "$DIAG_UDC" > "$DIAG_GADGET/UDC" || return 1
    DIAG_IFACE=$(cat "$DIAG_GADGET/functions/ncm.usb0/ifname") || return 1
    case "$DIAG_IFACE" in ''|*[!A-Za-z0-9_.:-]*) return 1 ;; esac
    timeout -s KILL 3 ip link set lo up || return 1
    timeout -s KILL 3 ip addr add 192.168.7.2/30 dev "$DIAG_IFACE" || return 1
    timeout -s KILL 3 ip link set "$DIAG_IFACE" up || return 1
    printf 'UDC=%s interface=%s phone=192.168.7.2/30\n' "$DIAG_UDC" "$DIAG_IFACE"
    printf 'ACM enumerates only; it is NOT an early console or command reader.\n'
}
