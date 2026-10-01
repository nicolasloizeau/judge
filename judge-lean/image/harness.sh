#!/bin/sh
# judge-lean in-sandbox harness. POSIX sh. Entrypoint of the judge-lean image,
# and what the local backend runs on the host with the same arguments.
#
#   harness.sh elab  CPU_S MEM_BYTES FSIZE_BYTES HEARTBEATS THREADS SEARCH DIR MODULE
#       lean --root=DIR DIR/MODULE.lean -o DIR/MODULE.olean, with Lean's own
#       stdout redirected to stderr: nothing an elaboration stage prints is
#       ever read as a result.
#   harness.sh check CPU_S MEM_BYTES SEARCH MODE NONCE ALLOW
#       judge-lean-check --mode MODE --nonce NONCE --allow ALLOW; its one
#       framed JSON line stays on stdout.
#
# SEARCH is a colon-separated list of directories prepended to LEAN_PATH (the
# scratch directories holding Verifier.olean / Solution.olean).
#
# The script never interprets output. It applies best-effort *soft* limits
# (the container policy / the local backend set the hard ones), runs the one
# program the stage is about, reports the stage's CPU time and sampled memory
# on stderr as `@@judge-times` / `@@judge-mem` lines, and propagates the exit
# code. MEM_BYTES is informational here: Lean maps gigabytes of read-only
# oleans and reserves large thread stacks, so neither RLIMIT_AS nor
# RLIMIT_DATA can be set to anything sane -- the memory cap is the container's
# cgroup limit (gVisor) or the backend's RSS watchdog (local).

set -u

: "${JUDGE_LEAN:=lean}"
: "${JUDGE_LEAN_CHECK:=judge-lean-check}"
: "${LEAN_PATH:=}"

stage=$1; shift

apply_limits() {
    # $1 = cpu seconds, $2 = fsize bytes (may be empty)
    ulimit -S -t "$1" 2>/dev/null || true
    if [ -n "${2:-}" ]; then
        # dash and busybox count 512-byte blocks, bash 1024-byte blocks.
        if [ -n "${BASH_VERSION:-}" ]; then blocks=$(($2 / 1024)); else blocks=$(($2 / 512)); fi
        ulimit -S -f "$blocks" 2>/dev/null || true
    fi
}

# Sample the cgroup's memory usage while the stage runs (gVisor exposes the
# live figure but no peak). Pure diagnostics; the backend takes the max.
sample_memory() {
    f=/sys/fs/cgroup/memory/memory.usage_in_bytes
    [ -r "$f" ] || f=/sys/fs/cgroup/memory.current
    [ -r "$f" ] || return 0
    while :; do
        echo "@@judge-mem $(cat "$f")" >&2
        sleep 0.25
    done
}

report_times() {
    # Second line of `times` is the children's user and system time.
    times | sed -n '2s/^/@@judge-times /p' >&2
}

case "$stage" in
    elab)
        cpu=$1; mem=$2; fsize=$3; heartbeats=$4; threads=$5; search=$6; dir=$7; module=$8
        [ -n "$search" ] && export LEAN_PATH="$search${LEAN_PATH:+:$LEAN_PATH}"
        apply_limits "$cpu" "$fsize"
        sample_memory & sampler=$!
        "$JUDGE_LEAN" \
            --root="$dir" "$dir/$module.lean" -o "$dir/$module.olean" \
            --threads="$threads" -D maxHeartbeats="$heartbeats" 1>&2
        rc=$?
        kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null
        report_times
        exit $rc
        ;;
    check)
        cpu=$1; mem=$2; search=$3; mode=$4; nonce=$5; allow=$6
        [ -n "$search" ] && export LEAN_PATH="$search${LEAN_PATH:+:$LEAN_PATH}"
        apply_limits "$cpu" ""
        sample_memory & sampler=$!
        "$JUDGE_LEAN_CHECK" --mode "$mode" --nonce "$nonce" --allow "$allow"
        rc=$?
        kill "$sampler" 2>/dev/null; wait "$sampler" 2>/dev/null
        report_times
        exit $rc
        ;;
    *)
        echo "harness.sh: unknown stage '$stage'" >&2
        exit 2
        ;;
esac
