#!/bin/sh
# Source-only helpers; tests pass a temporary sysfs fixture, never real hardware.
# 70 C is an intentionally conservative diagnostic SoC/PMIC stop condition,
# not an OPPO battery charge curve, skin-temperature limit, or safety guarantee.
thermal_snapshot() {
    local sysroot="$1" outfile="$2" file value type count=0 too_hot=0
    : > "$outfile" || return 2
    for file in "$sysroot"/thermal_zone*/temp; do
        [ -r "$file" ] || continue
        value=
        IFS= read -r value < "$file" || [ -n "$value" ] || continue
        case "$value" in ''|*[!0-9]*) continue ;; esac
        [ "${#value}" -le 6 ] || continue
        [ "$value" -gt 0 ] && [ "$value" -le 150000 ] || continue
        type=unknown
        if [ -r "${file%/temp}/type" ]; then
            IFS= read -r type < "${file%/temp}/type" || :
        fi
        printf '%s %s_mC\n' "$type" "$value" >> "$outfile"
        count=$((count + 1))
        [ "$value" -lt 70000 ] || too_hot=1
    done
    if [ "$count" -eq 0 ]; then
        printf 'ERROR no valid thermal readings; stop diagnostic\n' >> "$outfile"
        return 2
    fi
    if [ "$too_hot" -ne 0 ]; then
        printf 'ERROR diagnostic threshold 70000_mC reached\n' >> "$outfile"
        return 1
    fi
    printf 'OK %s readable zones; this is not battery charging protection\n' "$count" >> "$outfile"
}
