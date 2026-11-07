#!/usr/bin/env bash
# Sample shell script for the parser spike and adapter tests.
set -euo pipefail

readonly VERSION="1.2.0"
declare -A COLORS=([red]=31 [green]=32)

log() {
    local level="$1"; shift
    printf '[%s] %s\n' "$level" "$*" >&2
}

function die {
    log error "$@"
    exit 1
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            -v|--verbose) VERBOSE=1 ;;
            -o) OUT="$2"; shift ;;
            --) shift; break ;;
            *) die "unknown option: $1" ;;
        esac
        shift
    done
}

build() {
    local target
    for target in "$@"; do
        if [[ -f "$target.c" && ! -f "$target.o" ]]; then
            cc -c "$target.c" || die "compile failed"
        elif [ -d "$target" ]; then
            (cd "$target" && make)
        else
            log warn "skipping $target"
        fi
    done
    for ((i = 0; i < 3; i++)); do
        until ping -c1 localhost >/dev/null; do sleep 1; done
    done
    local count=$(( ${#COLORS[@]} > 1 ? 2 : 1 ))
    echo "$count"
}

cleanup() {
    rm -rf "${TMPDIR:-/tmp}/build.$$"
}
trap cleanup EXIT

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    parse_args "$@"
    build "${@:-app}"
fi

for f in *.txt; do
    [ -e "$f" ] || continue
    cat <<EOT
file: $f
EOT
done
