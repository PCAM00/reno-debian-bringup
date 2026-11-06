#!/bin/sh
# Source-only thermal helpers. Source has no device side effects.
# Tests use synthetic directories, never target sysfs or the target PID1.
# 70000 mC is a diagnostic stop threshold, NOT a battery/skin safety limit.
# Policy: at least one valid zone, ALL enumerated zones valid/cool, and no
# baseline zone disappears or changes type. This is not proof all sensors probed.
thermal_snapshot() {
    local sysroot="$1" outfile="$2" baseline="${3:-}"
    local dir file zone value type record oldzone oldtype oldvalue extra
    local seen=0 valid=0 invalid=0 missing=0 hot=0 rejected_reason
    : > "$outfile" || return 2
    for dir in "$sysroot"/thermal_zone*; do
        [ -d "$dir" ] || continue
        zone=${dir##*/}
        case "$zone" in thermal_zone[0-9]*) ;; *) continue ;; esac
        seen=$((seen + 1))
        file="$dir/temp"
        value= type= rejected_reason=
        if [ ! -f "$file" ] || [ ! -r "$file" ]; then
            rejected_reason=unreadable-temp
        elif ! IFS= read -r value < "$file" && [ -z "$value" ]; then
            rejected_reason=empty-temp
        else
            case "$value" in ''|*[!0-9]*) rejected_reason=invalid-temp ;; esac
            if [ -z "$rejected_reason" ]; then
                if [ "${#value}" -gt 6 ]; then
                    rejected_reason=temp-too-long
                else
                    # Decimal only, including a leading zero; no octal arithmetic.
                    while [ "${value#0}" != "$value" ]; do value=${value#0}; done
                    value=${value:-0}
                    if [ "$value" -eq 0 ] || [ "$value" -gt 150000 ]; then
                        rejected_reason=temp-out-of-range
                    fi
                fi
            fi
        fi
        if [ ! -r "$dir/type" ]; then
            rejected_reason=${rejected_reason:-unreadable-type}
        else
            IFS= read -r type < "$dir/type" || :
            case "$type" in ''|*[!A-Za-z0-9_.:+-]*) rejected_reason=${rejected_reason:-invalid-type} ;; esac
            [ "${#type}" -le 80 ] || rejected_reason=${rejected_reason:-type-too-long}
        fi
        if [ -n "$rejected_reason" ]; then
            invalid=$((invalid + 1))
            printf 'REJECT %s %s\n' "$zone" "$rejected_reason" >> "$outfile"
            continue
        fi
        valid=$((valid + 1))
        printf 'ZONE %s %s %s\n' "$zone" "$type" "$value" >> "$outfile"
        [ "$value" -lt 70000 ] || hot=1
    done
    if [ -n "$baseline" ] && [ -f "$baseline" ]; then
        while read -r oldzone oldtype extra; do
            [ -n "$oldzone" ] || continue
            case "$oldzone" in thermal_zone[0-9]*) ;; *) invalid=$((invalid + 1)); continue ;; esac
            record= value= type=
            # Compare observed names/types without evaluating any sysfs content.
            while read -r record zone value oldvalue extra; do
                if [ "$record" = ZONE ] && [ "$zone" = "$oldzone" ]; then
                    type=$value
                    break
                fi
            done < "$outfile"
            if [ "$type" != "$oldtype" ]; then
                missing=$((missing + 1))
                printf 'MISSING_OR_CHANGED %s\n' "$oldzone" >> "$outfile"
            fi
        done < "$baseline"
    fi
    printf 'COUNTS seen=%s valid=%s rejected=%s missing_or_changed=%s\n' \
        "$seen" "$valid" "$invalid" "$missing" >> "$outfile"
    if [ "$hot" -ne 0 ]; then
        printf 'STOP diagnostic-threshold-70000-mC\n' >> "$outfile"
        return 1
    fi
    if [ "$valid" -eq 0 ] || [ "$invalid" -ne 0 ] || [ "$missing" -ne 0 ]; then
        printf 'STOP incomplete-or-invalid-readings\n' >> "$outfile"
        return 2
    fi
    printf 'OK observed-zones-only-not-charging-protection\n' >> "$outfile"
    return 0
}

# Called ONLY after a successful snapshot. New valid zones become mandatory too.
# Existing members cannot be silently removed: thermal_snapshot checked them.
thermal_learn_baseline() {
    local snapshot="$1" baseline="$2" record zone type value extra count=0
    : > "$baseline.next" || return 2
    while read -r record zone type value extra; do
        [ "$record" = ZONE ] || continue
        printf '%s %s\n' "$zone" "$type" >> "$baseline.next" || return 2
        count=$((count + 1))
    done < "$snapshot"
    [ "$count" -gt 0 ] || return 2
    mv "$baseline.next" "$baseline"
}

# Do not change CPU frequency policy at runtime; verify the compiled default.
# Empty policy set is unknown (2), any non-powersave observed policy is wrong (1).
cpu_policy_snapshot() {
    local sysroot="$1" outfile="$2" dir governor count=0
    : > "$outfile" || return 2
    for dir in "$sysroot"/policy*; do
        [ -d "$dir" ] || continue
        governor=
        if [ ! -r "$dir/scaling_governor" ]; then
            printf 'REJECT %s unreadable-governor\n' "${dir##*/}" >> "$outfile"
            return 2
        fi
        IFS= read -r governor < "$dir/scaling_governor" || :
        count=$((count + 1))
        if [ "$governor" != powersave ]; then
            printf 'STOP %s governor-is-not-powersave\n' "${dir##*/}" >> "$outfile"
            return 1
        fi
        printf 'POLICY %s powersave\n' "${dir##*/}" >> "$outfile"
    done
    [ "$count" -gt 0 ] || { printf 'STOP no-cpu-policy\n' >> "$outfile"; return 2; }
    return 0
}
