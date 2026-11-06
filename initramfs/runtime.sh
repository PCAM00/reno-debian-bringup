#!/bin/sh
# Source-only state/stop coordination. No side effects when sourced.
# Production hooks are defined by target PID1; unit tests supply inert mocks.
# No function in this file can directly reboot, bind USB, or kill a process.
diag_now() {
    local value remainder
    value=
    IFS=' ' read -r value remainder < "${DIAG_UPTIME_FILE:-/proc/uptime}" || return 1
    value=${value%%.*}
    case "$value" in ''|*[!0-9]*) return 1 ;; esac
    [ "${#value}" -le 10 ] || return 1
    printf '%s\n' "$value"
}

diag_stage() {
    local reason="$1" now
    case "$reason" in ''|*[!A-Za-z0-9_.:+-]*) reason=invalid-stage ;; esac
    now=$(diag_now) || now=unknown
    DIAG_SEQ=$((${DIAG_SEQ:-0} + 1))
    # At most 256 event records within the fixed 180-second diagnostic.
    if [ "$DIAG_SEQ" -le 256 ]; then
        printf '%s %s %s\n' "$DIAG_SEQ" "$now" "$reason" >> "$DIAG_RUN/stages.log"
    fi
    printf 'revision=%s\nsequence=%s\nuptime=%s\nstage=%s\n' \
        "$DIAG_REVISION" "$DIAG_SEQ" "$now" "$reason" > "$DIAG_RUN/state.next" || return 1
    mv "$DIAG_RUN/state.next" "$DIAG_RUN/state.txt"
}

diag_deadline_reached() {
    local now="$1" start="$2" seconds="$3"
    case "$now:$start:$seconds" in *[!0-9:]*|::*|*::*) return 0 ;; esac
    [ -n "$now" ] && [ -n "$start" ] && [ -n "$seconds" ] || return 0
    [ "$now" -ge "$start" ] || return 0
    [ "$((now - start))" -ge "$seconds" ]
}

# Synchronous: the caller is PID1, not a monitor subshell. Each hook failure
# is logged and does not skip the following cleanup attempt. A returning reboot
# is never reported as success or as restoration of the original Android boot.
diag_stop_once() {
    local reason="$1" failed=0
    [ "${DIAG_STOPPING:-0}" = 0 ] || return 0
    DIAG_STOPPING=1
    case "$reason" in ''|*[!A-Za-z0-9_.:+-]*) reason=invalid-stop-reason ;; esac
    printf '%s\n' "$reason" > "$DIAG_RUN/stop-requested"
    diag_stage "stopping-$reason" || :
    diag_terminate_children || failed=1
    diag_unbind_usb || failed=1
    if [ "$failed" -eq 0 ]; then
        diag_stage diagnostic-services-stopped || :
    else
        diag_stage stop-attempt-incomplete || :
    fi
    diag_stage requesting-ordinary-reboot-not-rollback || :
    diag_request_reboot
    local reset_result=$?
    diag_stage "reboot-returned-$reset_result-no-services-restarted" || :
    return 0
}
