#!/usr/bin/env python3
"""Post-run change detection for haejwo's two reviewer runners.

  snapshot <workdir> <outfile> [artifact...]   file-backed BEFORE/AFTER state
  compare  <before.json> <after.json>          the verdict + coverage flags

Scope, honestly: HEAD, tracked file status AND per-path working-tree
fingerprints, `git diff` / `git diff --cached` digests, and the CONTENTS of
untracked files (sorted, first 2000; presence is covered for all of them).
Runner-owned artifacts — the list the ENTRYPOINT passes in — are excluded from
the status, untracked and diff-digest inputs alike. NOT covered: global/user
config, ignored files, anything outside the repo. Concurrent writers are not
distinguished: a detected change means "something changed", never "the
reviewer did it". Any error exits non-zero; a read error must NEVER be
mistaken for "nothing changed".
"""
import hashlib
import json
import os
import subprocess
import sys


def cmd_snapshot(argv):
    workdir, outfile = argv[0], argv[1]
    artifacts = [p for p in argv[2:] if p]

    def git(*args):
        r = subprocess.run(["git", "-C", workdir] + list(args), capture_output=True)
        if r.returncode != 0:
            detail = r.stderr.decode("utf-8", "replace").strip() or ("rc=%d" % r.returncode)
            raise RuntimeError("git %s: %s" % (" ".join(args), detail))
        return r.stdout

    def dec(b):
        return b.decode("utf-8", "surrogateescape")

    state = {"unreadable": 0}

    def fingerprint(path_abs):
        try:
            if os.path.islink(path_abs):
                return "link:" + hashlib.sha1(
                    os.readlink(path_abs).encode("utf-8", "surrogateescape")).hexdigest()
            h = hashlib.sha1()
            with open(path_abs, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            return h.hexdigest()
        except Exception:
            state["unreadable"] += 1
            return "unreadable"

    try:
        # Paths from git are repo-ROOT relative; resolve the root so exclusions
        # and pathspecs line up even when the runner is invoked from a subdir.
        # ONLY the trailing newline git appends — a directory name may legally
        # end in a space, and .strip() would silently point every path elsewhere.
        root = dec(git("rev-parse", "--show-toplevel"))
        if root.endswith("\n"):
            root = root[:-1]
        workdir = root
        root_real = os.path.realpath(root)

        excluded = set(os.path.realpath(p) for p in artifacts)
        rel_excludes = []
        for p in sorted(excluded):
            rel = os.path.relpath(p, root_real)
            if rel != ".." and not rel.startswith(".." + os.sep):
                rel_excludes.append(rel)

        def is_excluded(rel):
            return os.path.realpath(os.path.join(root_real, rel)) in excluded

        try:
            head = dec(git("rev-parse", "--verify", "HEAD")).strip()
        except RuntimeError:
            head = "unborn"  # a repo with no commits is not a git error

        # --untracked-files=all: a collapsed `newdir/` entry would hide which
        # files appeared, and runner artifacts written into a fresh directory
        # could not be excluded.
        status_raw = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        # Runner-owned artifacts are excluded from the diff digests by pathspec —
        # a reply file written over a TRACKED path would otherwise self-trip the
        # gate.
        pathspec = []
        if rel_excludes:
            pathspec = ["--", "."] + [":(exclude,literal)%s" % r for r in rel_excludes]
        diff_sha = hashlib.sha1(git(*(["diff"] + pathspec))).hexdigest()
        cached_sha = hashlib.sha1(git(*(["diff", "--cached"] + pathspec))).hexdigest()
        others_raw = git("ls-files", "--others", "--exclude-standard", "-z")

        entries = [e for e in status_raw.split(b"\0") if e]
        paths = {}
        i = 0
        while i < len(entries):
            e = dec(entries[i])
            xy, path = e[:2], e[3:]
            i += 1
            origin = None
            if xy and ("R" in xy or "C" in xy) and i < len(entries):
                origin = dec(entries[i])
                i += 1
            for p in (path, origin):
                if not p or is_excluded(p):
                    continue
                if xy == "??":
                    # presence only; contents live in the capped untracked map
                    paths[p] = [xy, None]
                elif "D" in xy:
                    paths[p] = [xy, "deleted"]
                else:
                    paths[p] = [xy, fingerprint(os.path.join(root_real, p))]

        others = sorted(set(dec(p) for p in others_raw.split(b"\0") if p))
        others = [p for p in others if not is_excluded(p)]
        truncated = len(others) > 2000
        if truncated:
            others = others[:2000]
        untracked = dict((p, fingerprint(os.path.join(root_real, p))) for p in others)

        # Counted across tracked AND untracked entries: "some files are opaque to
        # this gate" is a different limitation from "the untracked list was cut".
        coverage = {"truncated": truncated, "unreadable": state["unreadable"]}

        snap = {"head": head, "paths": paths, "untracked": untracked,
                "diff": diff_sha, "cached": cached_sha, "coverage": coverage}
        os.makedirs(os.path.dirname(os.path.abspath(outfile)), exist_ok=True)
        with open(outfile, "w", encoding="ascii") as f:
            json.dump(snap, f, ensure_ascii=True)
    except Exception as exc:
        sys.stderr.write("change detection: %s\n" % exc)
        sys.exit(1)


REQUIRED = ("head", "paths", "untracked", "diff", "cached", "coverage")


def cmd_compare(argv):
    def load(path):
        with open(path, encoding="ascii") as f:
            snap = json.load(f)
        if not isinstance(snap, dict):
            raise ValueError("snapshot is not an object: %s" % path)
        for key in REQUIRED:
            if key not in snap:
                raise KeyError("%s missing %r" % (path, key))
        if not isinstance(snap["coverage"], dict):
            raise ValueError("%s: coverage is not an object" % path)
        return snap

    try:
        before, after = load(argv[0]), load(argv[1])
    except Exception as exc:
        sys.stderr.write("change detection: unreadable snapshot: %s\n" % exc)
        sys.exit(2)

    def esc(path):
        # Keep the failure message to ONE line: repr only when the path carries
        # characters that would break it.
        if any(ch in path for ch in "\n\r\t") or any(ord(ch) < 32 for ch in path):
            return repr(path)
        return path

    items = []
    if before["head"] != after["head"]:
        items.append("HEAD %s→%s" % (before["head"][:7], after["head"][:7]))

    for key in ("paths", "untracked"):
        b, a = before[key], after[key]
        if not isinstance(b, dict) or not isinstance(a, dict):
            sys.stderr.write("change detection: malformed %s map\n" % key)
            sys.exit(2)
        for p in sorted(set(b) | set(a)):
            if b.get(p) != a.get(p):
                items.append(esc(p))

    # Aggregate digests only name the fact that SOMETHING tracked changed; emit
    # them only when no path-level item was found (they add nothing otherwise).
    if not items:
        if before["diff"] != after["diff"]:
            items.append("tracked contents (git diff)")
        if before["cached"] != after["cached"]:
            items.append("staged contents (git diff --cached)")

    seen, uniq = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    if len(uniq) > 20:
        rest = len(uniq) - 20
        uniq = uniq[:20] + ["+%d more" % rest]

    truncated = 0
    unreadable = 0
    for snap in (before, after):
        cov = snap["coverage"]
        if cov.get("truncated"):
            truncated = 1
        try:
            unreadable = max(unreadable, int(cov.get("unreadable") or 0))
        except Exception:
            pass

    sys.stdout.write("truncated=%d\nunreadable=%d\nchanged=%s\n"
                     % (truncated, unreadable, ", ".join(uniq)))


MODES = {"snapshot": cmd_snapshot, "compare": cmd_compare}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.stderr.write("detect.py: usage: detect.py snapshot|compare ...\n")
        sys.exit(2)
    MODES[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
