#!/usr/bin/env python3
"""Stale-runner self-forwarding for haejwo's two reviewer runners (2.18).

  version <plugin.json>                                   own manifest version
  target  <registry> <cache-root> <own-ver> <self> <name>  where to forward

WHY THIS EXISTS (measured 2026-09-28, khnp-rag session): a session started on
2.16.1, 2.17.0 was installed while it was open, and `/reload-plugins`
refreshed hooks and commands but did NOT re-inject the SessionStart brief. The
host kept invoking the LITERAL 2.16.1 runner path it still had in context —
old cache versions stay on disk — so three more consults ran with the 2.16.1
defect (`model=cli-default (identity unverified)`, no `config=` field). The
runner is the only component that learns the truth at the right moment, so it
forwards ITSELF to the installed version.

FAIL OPEN, ALWAYS. Every ambiguity, every read/parse error and every failed
validation prints NOTHING, and the caller then runs locally. Forwarding is a
convenience over a host's bookkeeping file; it must never be the reason a
review does not happen.

HOST-SCOPED. The registry path is derived from the INVOKED runner's own
`<plugins>` prefix, so a runner installed under `~/.codex/plugins/` can only
ever read `~/.codex/plugins/installed_plugins.json`. A Codex host keeps no
such registry today, which makes forwarding a documented no-op there — never a
cross-host lookup into Claude's registry.

NO LEXICAL VERSION ORDERING. The registry names exactly one installed entry;
that entry is followed when its version STRING differs from the runner's own,
downgrades included. "Higher" is not a judgment this layer is entitled to
make: the host installed what it installed.

Transport: values are terminated with the same sentinel the rest of the
library uses, because command substitution strips trailing newlines and an
install path may legally end in one.
"""
import json
import os
import stat
import sys

SENTINEL = "\x04__HJW_SNAP_END__"
# A plugin manifest and a plugin registry are small JSON files. The bound is
# what keeps a wrong/huge path (or a device node reached through a symlink)
# from being read into memory before a review that has not started yet.
MAX_BYTES = 1 << 20

PLUGIN_KEY = "haejwo@haejwo"
MANIFEST = os.path.join(".claude-plugin", "plugin.json")
# Every helper BOTH entrypoints check before they source anything — the
# identical `for _hjw_f in ...` list in codex_consult.sh and claude_consult.sh.
# A destination missing any one of them exits 3 AFTER it has already replaced
# this process, and the local fallback can no longer run: completeness is
# therefore decided HERE, before the exec.
#
# The list cannot be shared as one literal. The entrypoints check these files
# in order to be allowed to run python at all, so they cannot read the list
# from a python file that is itself on it. tests/test_hooks.py asserts that
# the three lists are equal.
#
# LEGACY DESTINATIONS. `forward.py` on this list is also what keeps forwarding
# from ever reaching a pre-2.18 install: those versions ship no lib/forward.py,
# so they can never be a target. That is not incidental — they also have no
# hop-marker removal, so forwarding into one would hand HJW_FORWARDED straight
# to the reviewer CLI. Only forwarding-aware destinations are followed; a
# downgrade to before 2.18 runs as invoked.
REQUIRED_LIB = ("consult_common.sh", "bounded.py", "snapshot.py", "detect.py",
                "config.py", "forward.py")


def _contained(path, root_real):
    """True when `path` RESOLVES inside `root_real`.

    The raw prefix test on the registry string is only a first filter: it
    accepts `<cache-root>/../../../../outside`, and it accepts a path whose
    components are symlinks pointing anywhere at all. Both are answered here,
    by normalization AND symlink resolution.

    NOT AN ATOMIC BOUNDARY. What is validated here and what `exec` runs later
    are two separate lookups, and anything able to write inside the cache can
    replace the file in between. These are ordinary path checks against
    accidental misconfiguration and casual tampering — not a defense against a
    hostile local user, who already owns the runner being invoked.
    """
    if not root_real or root_real == os.sep:
        return False
    try:
        real = os.path.realpath(path)
    except Exception:
        return False
    return real.startswith(root_real.rstrip(os.sep) + os.sep)


def _complete(install):
    """True when the destination carries the FULL helper set both entrypoints
    demand, each a regular readable file. A half-deleted cache directory is a
    real thing on disk, and so is an install from before these helpers
    existed."""
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
    """Parsed JSON at `path`, or None. Never raises: a missing, oversized,
    non-regular, unreadable or malformed file is all the same answer here —
    "no information", which means run locally."""
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
    """Registry entries for haejwo that live under THIS runner's own cache
    root. The value may be a list or a single object (both shapes have been
    seen); anything else, and any entry without usable strings, is dropped."""
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
        # UNDER the cache root, never the root itself — as WRITTEN (the cheap
        # first filter) and as RESOLVED (traversal and symlinks both).
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
    """Prints `<version><SENTINEL><target><SENTINEL>` when the invoked runner
    is stale and a COMPLETE replacement is installed — otherwise nothing."""
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
    # EXACTLY ONE installation, or nothing happens. Two entries disagreeing
    # about which version is installed is precisely the situation where a
    # guess would forward a review to the wrong code. Duplicates that say the
    # SAME thing are not a disagreement.
    if len({(v, os.path.normpath(p)) for v, p in found}) != 1:
        return
    version, install = found[0]
    # String inequality, both directions: an intentional downgrade is an
    # install like any other.
    if version == own_version:
        return
    target = os.path.join(install, "scripts", name)
    # The registry is host bookkeeping, not proof. The target must AGREE about
    # its own version and must be a complete install, or the runner stays
    # where it is: a half-deleted cache directory is a real thing on disk.
    if _version(os.path.join(install, MANIFEST)) != version:
        return
    if not _complete(install):
        return
    # The executable itself must resolve inside the cache root too: an install
    # directory that is contained says nothing about a runner inside it that is
    # a symlink to somewhere else.
    if not _contained(target, root_real):
        return
    try:
        st = os.stat(target)
        if not stat.S_ISREG(st.st_mode) or not os.access(target, os.X_OK):
            return
        # Physical identity, not path equality: a registry whose installPath
        # is a symlink to the invoked copy would otherwise exec this very
        # file again, and the hop marker would be the only thing standing
        # between that and an infinite loop.
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
        # Silence is the contract: the caller reads stdout, and an empty
        # stdout means "run locally".
        sys.exit(0)


if __name__ == "__main__":
    main()
