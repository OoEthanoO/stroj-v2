#!/usr/bin/env bash
# Send the judge's queued mail, from the host.
#
# The judge runs with no DNS and a blanket egress drop on its bridge — that is
# what denies submissions the network — so it cannot talk to a mail server
# itself. With STROJ_MAIL_TRANSPORT=spool it writes each message into a
# directory on its volume instead, and this drains that directory.
#
# Run it from a systemd timer every minute. It is safe to run concurrently with
# itself (it takes a lock) and safe to run when there is nothing to do.
#
#   OUTBOX   directory to drain      (default: the stroj-data volume's outbox)
#   SENDMAIL command to send with    (default: msmtp -t, then sendmail -t)
#
# Messages that fail are left where they are and retried on the next tick. A
# message that has failed for longer than STALE_HOURS is reported, because a
# confirmation link nobody can use expires anyway and the member is waiting.
set -euo pipefail

VOLUME="${VOLUME:-stroj-data}"
STALE_HOURS="${STALE_HOURS:-6}"

if [ -z "${OUTBOX:-}" ]; then
    mountpoint="$(docker volume inspect "$VOLUME" --format '{{ .Mountpoint }}' 2>/dev/null || true)"
    if [ -z "$mountpoint" ]; then
        echo "cannot find the $VOLUME volume; set OUTBOX to the spool directory" >&2
        exit 1
    fi
    OUTBOX="$mountpoint/outbox"
fi

[ -d "$OUTBOX" ] || exit 0

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
exec 9>"${OUTBOX}/.lock"
flock -n 9 || exit 0

sent=0
failed=0
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
[ "$failed" -gt 0 ] && exit 1
exit 0
