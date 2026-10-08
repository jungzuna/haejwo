#!/usr/bin/env python3
"""Post-run change detection and the artifact guard for haejwo's two reviewer runners.

  snapshot  <workdir> <outfile> [artifact...]   file-backed BEFORE/AFTER state
  compare   <before.json> <after.json>          the verdict + coverage flags
  artifacts [--dir] <cwd> <path>...             refuse artifacts inside the repo (2.21)

Scope: HEAD, tracked status AND per-path fingerprints, `git diff`/`--cached` digests, untracked CONTENTS
(sorted, first 2000; presence for all); the entrypoint's artifacts are excluded. NOT covered: global/user
config, ignored files, anything outside the repo; concurrent writers are not distinguished. Any error exits
non-zero — a read error is NEVER "nothing changed". `artifacts` exits 0 when every path lies outside the
worktree of <cwd> and its git dirs (lexically and resolved), else 2 with one line; other worktrees and a
hostile concurrent replacement are not covered.
*[origin: a cold read found that `-o` naming a tracked file was truncated by the log header and `rm -f` —
and change detection excluded it BY DESIGN, so the overwrite was silent]*
"""
import hashlib
import json
import os
import stat
import subprocess
import sys


def cmd_snapshot(argv):
    workdir, outfile = argv[0], argv[1]
    artifacts = [p for p in argv[2:] if p]

    def git(*args):
        # *[origin: `git status` may refresh the reviewed repo's .git/index — an indirect write and lock contention]*
        r = subprocess.run(["git", "--no-optional-locks", "-C", workdir] + list(args), capture_output=True)
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
        # Paths from git are repo-ROOT relative (the runner may run from a subdir). Strip ONLY git's
        # trailing newline: a directory name may end in a space.
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

        # --untracked-files=all: a collapsed `newdir/` would hide which files appeared and defeat exclusion.
        status_raw = git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        # Artifacts excluded by pathspec: a reply written over a TRACKED path would otherwise self-trip the gate.
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

        # Across tracked AND untracked: "opaque files" is a different limitation from "the list was cut".
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
        # Keep the failure message ONE line: repr only paths carrying line-breaking characters.
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

    # Aggregate digests only when no path-level item was found (they add nothing otherwise).
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


ARTIFACT_HINT = "pass -o with a path outside it (the session scratchpad, for example)"
ARTIFACT_DIR_HINT = "set TMPDIR to a directory outside it"


def cmd_artifacts(argv):
    # Default: each <path> is a FILE (refused inside the repo, or existing as anything but a single-link
    # regular file). --dir: each is a mktemp DIRECTORY — only containment (mktemp picks an unknown name).
    def esc(path):
        # One refusal LINE, whatever the path holds.
        if any(ord(ch) < 32 for ch in path):
            return repr(path)
        return path

    def refuse(reason, path, hint=ARTIFACT_HINT):
        sys.stderr.write("%s: %s — %s\n" % (reason, esc(path), hint))
        sys.exit(2)

    dir_mode = bool(argv) and argv[0] == "--dir"
    if dir_mode:
        argv = argv[1:]
    if not argv:
        sys.stderr.write("detect.py: usage: detect.py artifacts [--dir] <cwd> <path>...\n")
        sys.exit(2)
    try:
        cwd = os.path.abspath(argv[0] or os.getcwd())
    except Exception as exc:
        sys.stderr.write("cannot resolve the invoking directory: %s\n" % exc)
        sys.exit(2)
    paths = [p for p in argv[1:] if p]

    def git(*args):
        r = subprocess.run(["git", "-C", cwd] + list(args), capture_output=True)
        out = r.stdout.decode("utf-8", "surrogateescape")
        # ONLY the newline git appends: a directory name may end in whitespace.
        if out.endswith("\n"):
            out = out[:-1]
        return r.returncode, out, r.stderr.decode("utf-8", "replace").strip()

    rc, inside, err = git("rev-parse", "--is-inside-work-tree")
    if rc != 0:
        if rc == 128 and "not a git repository" in err:
            sys.exit(0)  # nothing to protect
        sys.stderr.write("cannot determine the reviewed repository (git rev-parse rc=%d): %s\n"
                         % (rc, err or "no detail"))
        sys.exit(2)
    queries = [("--absolute-git-dir",), ("--git-common-dir",)]
    if inside == "true":
        queries.insert(0, ("--show-toplevel",))
    roots = []
    for q in queries:
        rc, out, err = git("rev-parse", *q)
        if rc != 0 or not out:
            sys.stderr.write("cannot determine the reviewed repository (git rev-parse %s rc=%d): %s\n"
                             % (q[0], rc, err or "no output"))
            sys.exit(2)
        # --git-common-dir may be relative to <cwd>.
        root = os.path.normpath(os.path.join(cwd, out))
        roots.extend([root, os.path.realpath(root)])
    roots = sorted(set(roots))

    def inside_any(p):
        for root in roots:
            try:
                if os.path.commonpath([p, root]) == root:
                    return True
            except ValueError:
                continue  # different drives: never inside
        return False

    for path in paths:
        try:
            joined = os.path.join(cwd, path)
            candidates = (os.path.normpath(os.path.abspath(joined)), os.path.realpath(joined))
        except Exception as exc:
            refuse("cannot resolve artifact path (%s)" % exc, path)
        if dir_mode:
            if any(inside_any(c) for c in candidates):
                refuse("artifact directory is inside the reviewed repository", path,
                       ARTIFACT_DIR_HINT)
            continue
        if any(inside_any(c) for c in candidates):
            refuse("artifact path is inside the reviewed repository", path)
        # Followed through symlinks: a link to an outside file is the file; a dangling one was judged above.
        try:
            if os.path.exists(joined):
                st = os.stat(joined)
                if not stat.S_ISREG(st.st_mode):
                    refuse("artifact path exists and is not a regular file", path)
                if st.st_nlink > 1:
                    refuse("artifact path has more than one hard link", path)
        except SystemExit:
            raise
        except Exception as exc:
            refuse("cannot inspect artifact path (%s)" % exc, path)
    sys.exit(0)


MODES = {"snapshot": cmd_snapshot, "compare": cmd_compare, "artifacts": cmd_artifacts}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.stderr.write("detect.py: usage: detect.py snapshot|compare|artifacts ...\n")
        sys.exit(2)
    MODES[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
