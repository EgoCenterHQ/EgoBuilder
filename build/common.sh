#!/bin/bash
# =============================================================================
# build/common.sh — sourced by build/<arch>/EgOS4-<arch>.sh. Not run on its own.
# Versions + the overlay (the files of the repo tree).
# =============================================================================

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# --- versioning: bump VERSION on every change that ends up in the image ---
EGOS_VERSION="$(cat "$REPO/VERSION")"
IMMICH_VERSION="v3.2.2"        # ghcr.io/immich-app/immich-server tag (prod 2026-10-28)
JELLYFIN_VERSION="12.1"        # jellyfin/jellyfin tag (prod 2026-10-28)
BUILD_DATE="${BUILD_DATE:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"

# The build writes into the current directory (live-build config/chroot/binary ...): never into the repo itself, only into out/ or outside it.
egos_check_workdir() {
    case "$PWD/" in
        "$REPO"/out/*) ;;
        "$REPO"/*) echo "ERROR: run the build from an empty directory outside the repo (or under out/), not $PWD"; exit 1 ;;
    esac
}

# egos_render SRC DEST: copy a template, replacing every @NAME@ with the value of
# the shell variable NAME. An unset or empty NAME fails the build.
egos_render() {
    local n names
    names=$(grep -o '@[A-Z][A-Z0-9_]*@' "$1" | sort -u | tr -d @) || true
    cp "$1" "$2"
    for n in $names; do
        [ -n "${!n}" ] || { echo "ERROR: $1 uses @$n@, which is not set"; exit 1; }
        sed -i "s|@$n@|${!n}|g" "$2"
    done
}

# egos_copy SRC DEST: copy a tree (contents and modes, NOT the owner: as root the
# files become root's), then render the *.in templates in place (foo.in -> foo).
egos_copy() {
    local t d
    mkdir -p "$2"
    cp -R --preserve=mode "$1/." "$2/"
    find "$1" -type f -name '*.in' -printf '%P\n' | while IFS= read -r t; do
        d="$2/${t%.in}"
        egos_render "$1/$t" "$d" && chmod --reference="$1/$t" "$d" && rm -f "$2/$t"
    done
}

# egos_overlay DEST ARCH: the files EgOS adds to the Debian system.
#   rootfs/              -> /                        (shared)
#   stack/               -> /usr/local/share/stack   (Immich + Jellyfin)
#   extras/              -> /usr/local/share/stack/extras (optional, egos-extras)
#   webapp/webapp.py     -> /usr/local/bin/webapp.py (1st-boot setup page)
#   build/ARCH/overlay/  -> /                        (only this arch; wins over rootfs/)
egos_overlay() {
    local d="$1"
    EGOS_ARCH="$2"
    egos_copy "$REPO/rootfs" "$d"
    egos_copy "$REPO/stack"  "$d/usr/local/share/stack"
    egos_copy "$REPO/extras" "$d/usr/local/share/stack/extras"
    cp --preserve=mode "$REPO/webapp/webapp.py" "$d/usr/local/bin/webapp.py"
    if [ -d "$REPO/build/$EGOS_ARCH/overlay" ]; then
        egos_copy "$REPO/build/$EGOS_ARCH/overlay" "$d"
    fi
    echo "overlay ok: EgOS $EGOS_VERSION $EGOS_ARCH -> $d"
}
