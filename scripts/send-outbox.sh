#!/usr/bin/env bash
# Send the judge's queued mail, from the host.
#
# The judge runs with no DNS and a blanket egress drop on its bridge — that is
# what denies submissions the network — so it cannot talk to a mail server
# itself. With STROJ_MAIL_TRANSPORT=spool it writes each message into a
# directory on its volume instead, and this drains that directory.
#
# Run it with WATCH=1 and mail leaves as it is written: the drain waits on the
# directory, not on a clock, so a member who has just signed up has the link
# before they have finished switching windows. Without WATCH it makes one pass
# and exits, which is what a systemd timer wants. Both are safe to run
# concurrently with each other — they take the same lock — and safe to run when
# there is nothing to do.
#
#   OUTBOX      directory to drain      (default: the stroj-data volume's outbox)
#   SENDMAIL    command to send with    (default: msmtp -t, then sendmail -t)
#   WATCH       1 to stay up and drain on arrival, rather than exiting
#   POLL_SECS   how often to look when inotifywait is missing  (default: 2)
#   RETRY_SECS  how long to wait after a failed pass           (default: 60)
#
# Messages that fail are left where they are and retried on the next pass. A
# message that has failed for longer than STALE_HOURS is reported, because a
# confirmation link nobody can use expires anyway and the member is waiting.
set -euo pipefail

VOLUME="${VOLUME:-stroj-data}"
STALE_HOURS="${STALE_HOURS:-6}"
WATCH="${WATCH:-0}"
POLL_SECS="${POLL_SECS:-2}"
RETRY_SECS="${RETRY_SECS:-60}"
[ "${1:-}" = "--watch" ] && WATCH=1

if [ -z "${OUTBOX:-}" ]; then
    mountpoint="$(docker volume inspect "$VOLUME" --format '{{ .Mountpoint }}' 2>/dev/null || true)"
    if [ -z "$mountpoint" ]; then
        echo "cannot find the $VOLUME volume; set OUTBOX to the spool directory" >&2
        exit 1
    fi
    OUTBOX="$mountpoint/outbox"
fi

if [ ! -d "$OUTBOX" ]; then
    # A one-shot run has nothing to do. A watcher started before the judge has
    # ever spooled — a fresh box, or a service that comes up first — waits for
    # the directory rather than exiting into a restart loop.
    [ "$WATCH" = 1 ] || exit 0
    while [ ! -d "$OUTBOX" ]; do sleep "$POLL_SECS"; done
fi

if [ -z "${SENDMAIL:-}" ]; then
    if command -v msmtp >/dev/null 2>&1; then
        SENDMAIL="msmtp -t"
    elif command -v sendmail >/dev/null 2>&1; then
        SENDMAIL="sendmail -t"
    else
        echo "no msmtp or sendmail on PATH; set SENDMAIL" >&2
        exit 1
    fi
fi

# One drainer at a time: the timer can fire again while a slow relay is still
# being talked to, and sending a message twice is worse than sending it late.
# A watcher holds this for as long as it runs, which is what lets the old timer
# stay installed beside one without either sending anything twice.
exec 9>"${OUTBOX}/.lock"
flock -n 9 || exit 0

# One pass over the directory. Non-zero if anything was left behind, so the
# caller can tell an empty outbox from a relay that is refusing.
drain() {
    local sent=0 failed=0 message age_hours
    for message in "$OUTBOX"/*.eml; do
        # The glob is literal when the directory is empty.
        [ -e "$message" ] || break
        if $SENDMAIL < "$message"; then
            rm -f "$message"
            sent=$((sent + 1))
        else
            failed=$((failed + 1))
            age_hours=$(( ( $(date +%s) - $(stat -c %Y "$message") ) / 3600 ))
            if [ "$age_hours" -ge "$STALE_HOURS" ]; then
                echo "stuck for ${age_hours}h: $(basename "$message")" >&2
            fi
        fi
    done
    [ "$sent" -gt 0 ] && echo "sent $sent message(s)"
    [ "$failed" -eq 0 ]
}

# Wait for the next message to land. inotifywait returns on the rename the
# spooler finishes each message with; without it a two-second poll is close
# enough that nobody watching an inbox can tell the difference. Either way the
# wait is bounded, so an event missed between passes costs a delay rather than
# a message that never goes out.
wait_for_mail() {
    if command -v inotifywait >/dev/null 2>&1; then
        inotifywait -qq -t 60 -e close_write -e moved_to "$OUTBOX" >/dev/null 2>&1 || true
    else
        sleep "$POLL_SECS"
    fi
}

if [ "$WATCH" = 1 ]; then
    echo "watching $OUTBOX"
    while true; do
        # A failed pass backs off rather than spinning: the relay is down, and
        # retrying as fast as the loop goes round helps nobody.
        if drain; then wait_for_mail; else sleep "$RETRY_SECS"; fi
    done
fi

drain
