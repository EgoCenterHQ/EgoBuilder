#!/bin/bash
# make check: syntax of every shell/Python file, and a dry run of the overlay
# (build/common.sh egos_overlay), as the amd64 build would stage it.
set -e
cd "$(dirname "$0")/.."

fail=0
while IFS= read -r f; do
    case "$(head -1 "$f")" in
        '#!/bin/bash'*|'#!/bin/sh'*|'#!/usr/bin/env bash'*)
            bash -n "$f" || { echo "FAIL $f"; fail=1; } ;;
        '#!/usr/bin/env python3'*|'#!/usr/bin/python3'*)
            python3 -c 'import ast,sys; ast.parse(open(sys.argv[1]).read(), sys.argv[1])' "$f" \
                || { echo "FAIL $f"; fail=1; } ;;
    esac
done < <(find build rootfs stack webapp extras tools -type f | sort)
[ "$fail" = 0 ] && echo "ok: syntax"

T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
. build/common.sh
for a in amd64; do
    ( egos_overlay "$T/$a" "$a" ) || { echo "FAIL overlay $a"; fail=1; continue; }
    left=$(grep -rlE '@[A-Z][A-Z0-9_]*@' "$T/$a" || true)
    [ -z "$left" ] || { echo "FAIL $a: unrendered @NAME@ in: $left"; fail=1; }
    grep -qx "EGOS_VERSION=$EGOS_VERSION" "$T/$a/etc/egos-release" || { echo "FAIL $a: egos-release"; fail=1; }
done
[ "$fail" = 0 ] && echo "ok: overlay amd64 (EgOS $EGOS_VERSION)"
exit "$fail"
