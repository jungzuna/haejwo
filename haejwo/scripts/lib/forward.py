#!/usr/bin/env python3
"""Stale-runner self-forwarding for haejwo's two reviewer runners (2.18).

  version <plugin.json>                                   own manifest version
  target  <registry> <cache-root> <own-ver> <self> <name>  where to forward

*[origin: measured 2026-09-28 (khnp-rag): `/reload-plugins` did not re-inject the SessionStart brief, the
host kept invoking the literal 2.16.1 runner path, and three consults ran with the 2.16.1 defect]*
FAIL OPEN: any ambiguity or error prints NOTHING and the caller runs locally. HOST-SCOPED: the registry derives
from the runner's own <plugins> prefix. ONE ORDERING, the support floor: a differing registry version is followed,
downgrades included, unless below SUPPORTED_FLOOR — then the target field reads `floor <floor>` and the caller
stays local, saying so. Installs below SUPPORTED_FLOOR are unsupported as SOURCES too — a pre-floor runner that
hops here finds no snapshot.py and stays local on its own version. Values end with the library's sentinel (an
install path may end in a newline).
"""
import json
import os
import stat
import sys

SENTINEL = "\x04__HJW_SNAP_END__"
MAX_BYTES = 1 << 20  # keeps a wrong/huge path or device node out of memory
# Oldest install forwarded to (artifact guard, no snapshot dependency); no older-install compat remains (2.29).
SUPPORTED_FLOOR = "2.22.0"

PLUGIN_KEY = "haejwo@haejwo"
MANIFEST = os.path.join(".claude-plugin", "plugin.json")
# Every helper BOTH entrypoints check before sourcing (a test keeps the three lists equal). A destination
# missing one exits 3 after the exec, so completeness is decided HERE.
REQUIRED_LIB = ("consult_common.sh", "bounded.py", "detect.py",
                "config.py", "forward.py")


def _below(version, floor=SUPPORTED_FLOOR):
    """True unless `version` is a dotted numeric triple >= `floor` (unparsable counts as below)."""
    parts = version.split(".")
    try:
        return len(parts) != 3 or tuple(int(p) for p in parts) < tuple(int(p) for p in floor.split("."))
    except Exception:
        return True


def _contained(path, root_real):
    """True when `path` RESOLVES inside `root_real`. NOT atomic: guards misconfiguration, not a hostile owner."""
    if not root_real or root_real == os.sep:
        return False
    try:
        real = os.path.realpath(path)
    except Exception:
        return False
    return real.startswith(root_real.rstrip(os.sep) + os.sep)


def _complete(install):
    """True when every required helper is a regular readable file (half-deleted installs exist)."""
    lib = os.path.join(install, "scripts", "lib")
    for name in REQUIRED_LIB:
        path = os.path.join(lib, name)
        try:
            st = os.stat(path)
        except Exception:
            return False
        if not stat.S_ISREG(st.st_mode) or not os.access(path, os.R_OK):
            return False
    return True


def _load(path):
    """Parsed JSON at `path`, or None. Never raises: any bad file means "run locally"."""
    try:
        st = os.stat(path)
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_BYTES:
            return None
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def _version(manifest_path):
    """The `version` string in a plugin manifest, or "" when there is none."""
    obj = _load(manifest_path)
    if not isinstance(obj, dict):
        return ""
    v = obj.get("version")
    return v if isinstance(v, str) and v else ""


def _candidates(registry, cache_root, root_real):
    """haejwo registry entries under THIS runner's cache root; a list or one object (both seen)."""
    plugins = registry.get("plugins") if isinstance(registry, dict) else None
    if not isinstance(plugins, dict):
        return []
    raw = plugins.get(PLUGIN_KEY)
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    prefix = cache_root.rstrip("/") + "/"
    found = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        install = entry.get("installPath")
        ver = entry.get("version")
        if not isinstance(install, str) or not isinstance(ver, str):
            continue
        if not install or not ver:
            continue
        # UNDER the cache root, never the root itself — as written (cheap filter) and as resolved.
        if not install.startswith(prefix) or not install[len(prefix):].strip("/"):
            continue
        if not _contained(install, root_real):
            continue
        found.append((ver, install))
    return found


def cmd_version(argv):
    v = _version(argv[0]) if argv else ""
    if v:
        sys.stdout.write(v + SENTINEL)


def cmd_target(argv):
    """Prints `<version><SENTINEL><target><SENTINEL>` for a COMPLETE replacement, else nothing."""
    if len(argv) < 5:
        return
    registry_path, cache_root, own_version, self_path, name = argv[:5]
    if not own_version or not name or os.sep in name:
        return
    registry = _load(registry_path)
    if registry is None:
        return
    root_real = os.path.realpath(cache_root)
    found = _candidates(registry, cache_root, root_real)
    # EXACTLY ONE installation (identical duplicates agree), or nothing: a guess forwards to the wrong code.
    if len({(v, os.path.normpath(p)) for v, p in found}) != 1:
        return
    version, install = found[0]
    # String inequality, both directions: an intentional downgrade is an install like any other.
    if version == own_version:
        return
    target = os.path.join(install, "scripts", name)
    # The registry is bookkeeping, not proof: the target must agree on its version and be complete.
    if _version(os.path.join(install, MANIFEST)) != version:
        return
    if _below(version):
        sys.stdout.write(version + SENTINEL + "floor " + SUPPORTED_FLOOR + SENTINEL)
        return
    if not _complete(install):
        return
    # The executable must resolve inside the cache root too (a contained dir may hold a symlinked runner).
    if not _contained(target, root_real):
        return
    try:
        st = os.stat(target)
        if not stat.S_ISREG(st.st_mode) or not os.access(target, os.X_OK):
            return
        # Physical identity: a symlinked installPath to this copy would re-exec it (only the marker stops a loop).
        if os.path.realpath(target) == os.path.realpath(self_path):
            return
    except Exception:
        return
    sys.stdout.write(version + SENTINEL + target + SENTINEL)


MODES = {"version": cmd_version, "target": cmd_target}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.stderr.write("forward.py: usage: forward.py version|target ...\n")
        sys.exit(2)
    try:
        MODES[sys.argv[1]](sys.argv[2:])
    except Exception:
        # Silence is the contract: an empty stdout means "run locally".
        sys.exit(0)


if __name__ == "__main__":
    main()
