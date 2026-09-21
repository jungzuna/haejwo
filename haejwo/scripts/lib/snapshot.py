#!/usr/bin/env python3
"""--snapshot capture for haejwo's two reviewer runners.

Three modes, each invoked under the runners' wall clock:

  preflight  <workdir> <meta>                 refuse BEFORE anything is created
  build      <root> <sha> <snap> <meta> [guard...]   capture into the worktree
  registered <orig> <want>                    is <want> a worktree git knows?

Every step's status is checked and EVERY failure refuses before the paid call:
a snapshot that is silently incomplete is worse than no snapshot, because the
reviewer's conclusions would be about a repository that never existed. Values
the shell reads back are sentinel-terminated — a repository path may legally
end in a newline and command substitution would eat it.
*[origin: B8 snapshot spec 2 — fail closed and loud on capture]*
"""
import hashlib
import os
import shutil
import subprocess
import sys
import time

SENT = "\x04__HJW_SNAP_END__"
CAP = 2000


def git(cwd, *args):
    return subprocess.run(["git", "-C", cwd] + list(args), capture_output=True)


def detail(r):
    return r.stderr.decode("utf-8", "replace").strip() or ("rc=%d" % r.returncode)


def make_meta_writer(meta):
    def write_meta(name, value, sentinel=True):
        # Sentinel-terminated so the shell can read the value back byte-exactly.
        try:
            with open(os.path.join(meta, name), "w", encoding="utf-8",
                      errors="surrogateescape") as f:
                f.write(value + SENT if sentinel else value)
            return True
        except Exception:
            return False

    return write_meta


def cmd_preflight(argv):
    workdir, meta = argv[0] or os.getcwd(), argv[1]
    write_meta = make_meta_writer(meta)

    def refuse(reason):
        if not write_meta("refuse", reason):
            # The meta dir itself is unwritable — say so on stderr rather than
            # exiting with a status the caller cannot explain.
            sys.stderr.write("snapshot capture: %s\n" % reason)
        sys.exit(3)

    r = git(workdir, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        refuse("not a git repository (%s)" % detail(r))
    # ONLY the trailing newline git appends — a directory name may legally end in
    # a space, and .strip() would silently point every path elsewhere.
    root = r.stdout.decode("utf-8", "surrogateescape")
    if root.endswith("\n"):
        root = root[:-1]
    if not root:
        refuse("git could not name the repository root")

    r = git(root, "rev-parse", "--verify", "HEAD")
    if r.returncode != 0:
        refuse("unborn HEAD — there is no commit to snapshot")
    sha = r.stdout.decode("ascii", "replace").strip()

    r = git(root, "diff", "--name-only", "--diff-filter=U")
    if r.returncode != 0:
        refuse("cannot list unmerged paths (%s)" % detail(r))
    if r.stdout.strip():
        refuse("unresolved merge conflicts in the working tree — resolve them first")

    # A gitlink is a POINTER to another repository: `worktree add` recreates the
    # pointer but never the submodule's contents, so the reviewer would read an
    # empty directory and believe it.
    for args in (("ls-tree", "-r", "HEAD", "-z"), ("ls-files", "-s", "-z")):
        r = git(root, *args)
        if r.returncode != 0:
            refuse("cannot inspect the tree/index (%s)" % detail(r))
        for entry in r.stdout.split(b"\0"):
            if entry.startswith(b"160000 "):
                refuse("gitlink (submodule) entry present — a worktree snapshot "
                       "cannot reproduce its contents")

    # An embedded untracked repository is listed by git as a single directory and
    # would be copied as an opaque blob (or not at all) — refuse instead of
    # guessing which of the two the caller meant.
    r = git(root, "ls-files", "--others", "--exclude-standard", "-z")
    if r.returncode != 0:
        refuse("cannot list untracked files (%s)" % detail(r))
    for raw in r.stdout.split(b"\0"):
        if not raw:
            continue
        rel = raw.decode("utf-8", "surrogateescape").rstrip("/")
        p = os.path.join(root, rel)
        # islink first: a symlink is copied AS A LINK and never followed, so a
        # repository behind one is never traversed by this capture.
        if not os.path.islink(p) and os.path.isdir(p) and os.path.exists(os.path.join(p, ".git")):
            refuse("embedded untracked repository: %s" % rel)

    if not write_meta("orig", root) or not write_meta("sha", sha):
        refuse("cannot record the repository root and HEAD")


def cmd_build(argv):
    root, sha, snap, meta = argv[0:4]
    guard = [p for p in argv[4:] if p]
    write_meta = make_meta_writer(meta)

    def refuse(reason):
        if not write_meta("refuse", reason):
            sys.stderr.write("snapshot capture: %s\n" % reason)
        sys.exit(3)

    def gitck(cwd, *args):
        # There is no "best effort" inside a capture: a git call whose status we
        # ignored would put an unknown amount of the repository into the snapshot.
        r = git(cwd, *args)
        if r.returncode != 0:
            refuse("git %s failed: %s" % (" ".join(args), detail(r)))
        return r.stdout

    def under(child, parent):
        c, p = os.path.realpath(child), os.path.realpath(parent)
        return c == p or c.startswith(p.rstrip(os.sep) + os.sep)

    # The snapshot must never live inside the repository it copies: it would show
    # up in the original's own status and in its own change detection.
    if under(snap, root):
        refuse("the temp directory lies inside the repository (%s) — point TMPDIR "
               "outside it" % root)
    for p in guard:
        if under(p, snap):
            refuse("a caller path lies inside the snapshot and would be deleted "
                   "with it: %s" % p)

    gitck(root, "worktree", "add", "--detach", snap, sha)

    def stamp():
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def tracked_patch():
        # Working tree vs the captured commit: this reproduces the NET content,
        # NOT the separate staged/unstaged states (documented in the brief note).
        return gitck(root, "diff", "--binary", "--no-ext-diff", "--no-textconv", sha)

    def untracked_list():
        raw = gitck(root, "ls-files", "--others", "--exclude-standard", "-z")
        return sorted(x for x in raw.split(b"\0") if x)

    def fingerprint(path_abs):
        if os.path.islink(path_abs):
            return "link:" + hashlib.sha1(
                os.readlink(path_abs).encode("utf-8", "surrogateescape")).hexdigest()
        h = hashlib.sha1()
        with open(path_abs, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()

    start = stamp()
    patch = tracked_patch()
    patch_path = os.path.join(meta, "capture.patch")
    try:
        with open(patch_path, "wb") as f:
            f.write(patch)
    except Exception as exc:
        refuse("cannot stage the working-tree patch: %s" % exc)
    if patch:
        r = git(snap, "apply", "--binary", "--index", patch_path)
        if r.returncode != 0:
            refuse("cannot replay the working-tree patch into the snapshot: %s" % detail(r))
    names = gitck(root, "diff", "--name-only", "--no-ext-diff", "--no-textconv", "-z", sha)
    n_paths = len([x for x in names.split(b"\0") if x])

    eligible = untracked_list()
    n_eligible = len(eligible)
    partial = n_eligible > CAP
    selected = eligible[:CAP]
    src_prints = []
    dst_prints = []
    for raw in selected:
        rel = raw.decode("utf-8", "surrogateescape")
        src = os.path.join(root, rel)
        dst = os.path.join(snap, rel)
        try:
            before = fingerprint(src)
        except Exception as exc:
            # NEVER downgraded to a coverage note: an unreadable file is a hole in
            # the snapshot, not a cap omission.
            refuse("cannot read untracked file %r: %s" % (rel, exc))
        try:
            parent = os.path.dirname(dst)
            if parent:
                os.makedirs(parent, exist_ok=True)
            if os.path.islink(src):
                # Copied AS A LINK: following it could pull in content from
                # outside the repository and silently widen the snapshot.
                if os.path.lexists(dst):
                    os.remove(dst)
                os.symlink(os.readlink(src), dst)
            else:
                shutil.copy2(src, dst, follow_symlinks=False)
        except Exception as exc:
            refuse("cannot copy untracked file %r into the snapshot: %s" % (rel, exc))
        try:
            after = fingerprint(dst)
        except Exception as exc:
            refuse("cannot verify the copied file %r: %s" % (rel, exc))
        # The copy is not atomic either. Hashing only the SOURCE would certify
        # content the snapshot does not actually hold if a writer changed the file
        # mid-copy, so the destination is hashed too and the two must agree.
        if after != before:
            refuse("untracked file changed during capture: %s" % rel)
        src_prints.append((raw, before))
        dst_prints.append((raw, after))
    end = stamp()

    # The digest certifies what the snapshot HOLDS, so it is computed over the
    # destination fingerprints.
    digest = hashlib.sha256()
    digest.update(sha.encode("ascii"))
    digest.update(b"\0")
    digest.update(patch)
    digest.update(b"\0")
    for raw, fp in sorted(dst_prints):
        digest.update(raw + b"\t" + fp.encode("ascii") + b"\n")
    snapshot_digest = digest.hexdigest()

    # Drift re-check: capture is a sequence of git calls, not an atomic operation,
    # so a writer active in the original can tear it. Recompute from the ORIGINAL
    # and compare against the pre-copy source fingerprints; REFUSE rather than
    # review a state that never existed.
    drifted = tracked_patch() != patch or untracked_list() != eligible
    if not drifted:
        for raw, fp in src_prints:
            try:
                if fingerprint(os.path.join(root, raw.decode("utf-8", "surrogateescape"))) != fp:
                    drifted = True
                    break
            except Exception:
                drifted = True
                break
    if drifted:
        refuse("original changed during capture — retry")

    ignored_raw = gitck(root, "ls-files", "--others", "--ignored", "--exclude-standard",
                        "--directory", "-z")
    n_ignored = len([x for x in ignored_raw.split(b"\0") if x])

    tag = "snapshot=" + sha[:7]
    if n_paths or selected:
        tag += "+dirty(%d,%d%s)" % (n_paths, len(selected), ",partial" if partial else "")

    note = ("SNAPSHOT: this run executes in a detached snapshot of %s at %s — HEAD %s plus "
            "uncommitted changes captured between %s and %s (capture is not atomic). Paths "
            "under %s in the brief refer to the same files under %s; read the snapshot, never "
            "the original. Ignored untracked files are omitted (tracked files are included "
            "regardless of ignore rules), so dependencies and configuration may be missing: "
            "report a missing capability instead of installing anything or reading omitted "
            "files from the original. Preserved symlinks may resolve outside the snapshot; do "
            "not follow them. Do not write."
            % (root, snap, sha, start, end, root, snap))
    if partial:
        note += (" Untracked coverage is partial (first %d of %d eligible files)."
                 % (CAP, n_eligible))

    log = [
        "# ---- snapshot: %s ----" % snap,
        "# snapshot origin: %s" % root,
        "# snapshot HEAD: %s" % sha,
        "# snapshot capture: %s .. %s (NOT atomic; drift re-checked)" % (start, end),
        "# snapshot patch: bytes=%d paths=%d (net working-tree content, not staged/unstaged states)"
        % (len(patch), n_paths),
        "# snapshot untracked: copied=%d eligible=%d cap=%d" % (len(selected), n_eligible, CAP),
        "# snapshot digest: sha256 %s" % snapshot_digest,
        "# snapshot omissions: ignored_entries=%d (directories collapsed) cap_omitted=%d unreadable=0"
        % (n_ignored, max(0, n_eligible - len(selected))),
    ]

    # `log` is appended to $LOG verbatim, so it carries no sentinel.
    if not write_meta("tag", tag) or not write_meta("note", note) \
            or not write_meta("log", "\n".join(log) + "\n", sentinel=False):
        refuse("cannot record the snapshot disclosure")


def cmd_registered(argv):
    # Runs the listing ITSELF: a NUL-delimited porcelain stream cannot survive
    # command substitution (bash drops NULs), and the non-z form C-quotes exotic
    # paths. git prints its OWN resolved path, which may differ textually from
    # the mktemp path the runner holds, so compare realpaths.
    # Exit 0 = registered, 1 = definitely NOT registered, anything else = could
    # not tell — and the caller treats "could not tell" as registered.
    # *[origin: ship review Z1]*
    orig, want = argv[0], argv[1]

    def paths():
        # Preferred: NUL-delimited, never quoted (git >= 2.36).
        r = subprocess.run(["git", "-C", orig, "worktree", "list", "--porcelain", "-z"],
                           capture_output=True)
        if r.returncode == 0:
            return [f[len(b"worktree "):] for f in r.stdout.split(b"\0")
                    if f.startswith(b"worktree ")]
        # Fallback for a git without -z. A C-quoted entry is one this format
        # cannot round-trip, so refuse to guess: raise and be treated as
        # "could not tell" (fail-safe = registered).
        r = subprocess.run(["git", "-C", orig, "worktree", "list", "--porcelain"],
                           capture_output=True)
        if r.returncode != 0:
            raise RuntimeError("worktree list rc=%d" % r.returncode)
        out = []
        for line in r.stdout.split(b"\n"):
            if not line.startswith(b"worktree "):
                continue
            value = line[len(b"worktree "):]
            if value.startswith(b'"'):
                raise RuntimeError("C-quoted worktree path — cannot parse safely")
            out.append(value)
        return out

    try:
        target = os.path.realpath(want)
        for raw in paths():
            if os.path.realpath(raw.decode("utf-8", "surrogateescape")) == target:
                sys.exit(0)
        sys.exit(1)
    except Exception:
        # SystemExit is a BaseException, so the two exits above pass through.
        sys.exit(2)


MODES = {"preflight": cmd_preflight, "build": cmd_build, "registered": cmd_registered}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in MODES:
        sys.stderr.write("snapshot.py: usage: snapshot.py preflight|build|registered ...\n")
        sys.exit(2)
    MODES[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
