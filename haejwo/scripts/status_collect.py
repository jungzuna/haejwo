#!/usr/bin/env python3
"""haejwo status collector (read-only) for /haejwo:status.

  status_collect.py <data-dir> <session-id>

Prints ONE compact plain-text block: config (effective flags, env overrides),
this turn's counter, reviewer readiness from config, this session's hook
observations, delegations and anomalies. The HOST interprets; this script only
collects, so every status run reads the same files the same way.
*[origin: cold-loop cycle 2 D8 — the host performed a six-step JSON-reading
procedure by hand on every status request]*
Never writes, stdlib only, exit 0 always: a malformed file becomes a note in
the block, never a traceback. An empty or unsubstituted session id means
"unknown": the newest state file is shown, labeled, and observations are
summarized machine-wide only.
"""
import collections
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hjw_common import (  # noqa: E402
    OPEN_INTERVALS_FILE, STALE_DAYS, _safe_sid, command_name,
    gate_disabled_by_env, load_config_with_status, on_codex_host, state_file,
)

MAX_BYTES = 4 << 20  # observe() rotates at 200KB; anything larger is not ours
LAST_N = 10
# Env the hooks or the reviewer runners read; shown only when set.
ENV_KEYS = ("HAEJWO_GATE", "CODEX_MODEL", "CODEX_EFFORT", "CODEX_SANDBOX",
            "CODEX_TIMEOUT", "CLAUDE_MODEL", "HJW_CLAUDE_EFFORT", "CLAUDE_TIMEOUT")
# Record shapes the 2.26 hooks write; anything else is a "new shape".
BASE_KEYS = {"v", "hook", "agent_type", "agent_id", "sid", "decision", "ts"}
# 2.36.0: a subagent-exempt interval row (hjw_common._aggregate_locked)
INTERVAL_KEYS = {"via", "iid", "n", "first", "last"}
KNOWN_KEYS = {
    "gate": BASE_KEYS | {"tool", "path", "via", "offending"} | INTERVAL_KEYS,
    "bash_guard": BASE_KEYS | {"via", "target"} | INTERVAL_KEYS,
    # plan_marker_kind RETIRED 2.36.0: records from earlier sessions still parse
    "delegation": BASE_KEYS | {"subagent_type", "requested_model",
                               "plan_marker_kind", "tier_pin_check"},
    "report_check": BASE_KEYS | {"via", "chars"},  # chars: 2.33.0
    # 2.33.0 notes (one record per emitted line; `chars` counts exposure)
    "post_bash": BASE_KEYS | {"via", "chars"},
    # RETIRED 2.34.0: records from 2.33.0 sessions still parse
    "handback_nudge": BASE_KEYS | {"via", "chars"},
    # RETIRED 2.34.0: records from 2.33.0 sessions still parse
    "turn_checklist": BASE_KEYS | {"via", "chars"},
}


def _stamp(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
    except Exception:
        return "?"


def _read_json(path):
    """(object, note): note is None when the file parsed."""
    try:
        if os.path.getsize(path) > MAX_BYTES:
            return None, "too large"
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f), None
    except FileNotFoundError:
        return None, "absent"
    except Exception as e:
        return None, type(e).__name__


def _flag(on, why_off):
    return f"OFF ({why_off})" if why_off else ("ON" if on else "OFF")


def config_lines(data, cfg, status, on_codex):
    g = cfg["gate"]
    env_off = gate_disabled_by_env()
    lines = []
    if status == "malformed":
        lines.append("CONFIG MALFORMED: config.json does not parse — every hook fails "
                     "open (allows) until it is repaired")
    elif status == "absent":
        lines.append("config: absent — shipped defaults active")
    off = "env HAEJWO_GATE" if env_off else ("config malformed" if status == "malformed" else "")
    sub_off = off or ("" if g["enabled"] else "gate off")
    # cycle 3 B1: an invalid budget was replaced by None = edit gate off.
    budget = g["max_files_per_turn"]
    budget = "invalid (edit gate OFF)" if budget is None else budget
    # cycle 3 B4: Codex's spawn_agent is not hooked, so the key does nothing.
    dg = ("n/a (spawn_agent not hooked)" if on_codex
          else _flag(g["delegation_guard"], sub_off))
    lines.append(
        f"config: configured={'yes' if cfg.get('configured') is True else 'no'} "
        f"gate={_flag(g['enabled'], off)} budget={budget} "
        f"bash_guard={_flag(g['bash_guard'], sub_off)} "
        f"delegation_guard={dg} (effective)")
    tiers = cfg["models_codex" if on_codex else "models"]
    lines.append("tiers (%s): %s" % ("models_codex" if on_codex else "models", " ".join(
        f"{k}={tiers.get(k)}" for k in ("deep_reasoner", "default_worker", "task_worker"))))
    if cfg.get("_ignored"):
        lines.append("config ignored: " + "; ".join(cfg["_ignored"]))
    env = [f"{k}={os.environ[k]}" for k in ENV_KEYS if os.environ.get(k)]
    lines.append("env overrides: " + (", ".join(env) if env else "none"))
    return lines, (g["enabled"] and g["max_files_per_turn"] is not None
                   and not off)


def turn_line(data, sid, budget):
    if sid:
        path, label = state_file(data, sid), ""
    else:
        cands = [p for p in glob.glob(os.path.join(data, "state", "*.json"))
                 if not os.path.basename(p).startswith("__")]
        if not cands:
            return "this turn: session id unknown — no session-state file exists"
        path = max(cands, key=lambda p: os.path.getmtime(p))
        label = " (session id unknown — newest session-state file, may be another session's)"
    st, note = _read_json(path)
    if note == "absent":
        return f"this turn: 0/{budget} code edits (no state file yet){label}"
    if note or not isinstance(st, dict) or not isinstance(st.get("files"), list):
        return (f"this turn: state file {os.path.basename(path)} unreadable "
                f"({note or 'not a counter object'}) — the hooks treat it as 0{label}")
    files = [str(f) for f in st["files"]]
    shown = ", ".join(files[:5]) + (f", +{len(files) - 5} more" if len(files) > 5 else "")
    return f"this turn: {len(files)}/{budget} code edits{label}" + (f": {shown}" if files else "")


def reviewer_line(cfg, on_codex, now):
    c = cfg.get("codex") if isinstance(cfg.get("codex"), dict) else {}
    who = "claude" if on_codex else "codex"
    enabled = c.get("enabled") is True
    v = c.get("verified_at")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        verified = "never verified"
    elif v > now + 300:
        verified = f"{_stamp(v)} — IN THE FUTURE (anomaly)"
    else:
        age = int((now - v) // 86400)
        verified = f"{_stamp(v)} ({age}d ago)"
        if enabled and age > STALE_DAYS:
            verified += f" — re-run {command_name('setup', on_codex)} to re-verify"
    consent = c.get("danger_full_access_consented_at")
    return (f"reviewer ({who}): {'enabled' if enabled else 'disabled'} "
            f"model={c.get('model') or 'CLI default'} effort={c.get('effort') or 'runner default'} "
            f"sandbox={c.get('consult_sandbox') or 'unset'} "
            f"consent={_stamp(consent) if consent else 'none'} verified={verified}")


def load_observations(data):
    """(records in file order across .3, .2, .1 then current, unparsable line count, notes)."""
    recs, bad, notes = [], 0, []
    for name in ("observations.jsonl.3", "observations.jsonl.2",
                 "observations.jsonl.1", "observations.jsonl"):
        path = os.path.join(data, "state", name)
        try:
            if os.path.getsize(path) > MAX_BYTES:
                notes.append(f"{name} larger than {MAX_BYTES} bytes — skipped")
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        bad += 1
                        continue
                    if isinstance(r, dict):
                        recs.append(r)
                    else:
                        bad += 1
        except FileNotFoundError:
            continue
        except Exception as e:
            notes.append(f"{name} unreadable ({type(e).__name__})")
    return recs, bad, notes


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_interval(r):
    """A subagent-exempt interval row (2.36.0): `iid`, a count `n` >= 1 and
    the interval's first/last call time. Older one-call rows are not."""
    n = r.get("n")
    return (isinstance(r.get("iid"), str) and isinstance(n, int)
            and not isinstance(n, bool) and n >= 1
            and _num(r.get("first")) and _num(r.get("last")))


def _calls(r):
    return r["n"] if _is_interval(r) else 1


def load_open_intervals(data):
    """(rows for the intervals still open, note). Read BEFORE the log: an
    interval closed between the two reads then appears in the log with the
    same iid and a count at least as large (_collapse keeps the largest)."""
    if not data:
        return [], None
    obj, note = _read_json(os.path.join(data, "state", OPEN_INTERVALS_FILE))
    if note == "absent":
        return [], None
    ivs = list(obj.values()) if isinstance(obj, dict) else None
    if note or ivs is None or not all(isinstance(iv, dict) and _is_interval(iv)
                                      for iv in ivs):
        return [], (f"{OPEN_INTERVALS_FILE} unreadable ({note or 'not interval objects'}) "
                    "— subagent-exempt call counts may be partial")
    rows = []
    for iv in ivs:
        r = {"v": 2, "decision": "allow", "via": "subagent-exempt"}
        r.update({k: iv.get(k) for k in ("hook", "agent_type", "sid", "iid", "n",
                                         "first", "last")})
        r["ts"] = iv["last"]
        rows.append(r)
    return rows, None


SNAPSHOT_TRIES = 3
_SNAPSHOT_FILES = ("observations.jsonl.3", "observations.jsonl.2",
                   "observations.jsonl.1", "observations.jsonl", OPEN_INTERVALS_FILE)


def _files_sig(data):
    """(inode, size, mtime_ns) per observation file; None when absent."""
    sig = []
    for name in _SNAPSHOT_FILES:
        try:
            st = os.stat(os.path.join(data, "state", name))
            sig.append((st.st_ino, st.st_size, st.st_mtime_ns))
        except Exception:
            sig.append(None)
    return sig


def read_snapshot(data):
    """(pending, pending note, records, unparsable count, notes) from reads
    that saw no write. Hooks write without waiting for this reader (2.36.0
    diff review round 1): between the open-interval read and the log read a
    hook can open an interval and another raise its count (the count is then
    missing), and a rotation can move an unread generation onto a name
    already read (a base limitation of the 4-file read). So: open file, the
    generations, the open file again, with every file's stat before and
    after; a change retries, up to SNAPSHOT_TRIES reads; still changing ->
    the last read with a note that its counts may be incomplete."""
    for _ in range(SNAPSHOT_TRIES):
        before = _files_sig(data) if data else None
        pending, pnote = load_open_intervals(data)  # BEFORE the log (see there)
        recs, bad, notes = load_observations(data)
        again, _ = load_open_intervals(data)
        if not data or (again == pending and _files_sig(data) == before):
            return pending, pnote, recs, bad, notes
    notes = notes + [f"observation files changed during {SNAPSHOT_TRIES} reads (hooks writing "
                     "or rotating) — the counts below may be incomplete"]
    return pending, pnote, recs, bad, notes


def _collapse(recs):
    """Interval rows merged per iid (the opening row, a closing row, the open
    file): the largest n, earliest first, latest last. Other rows as given."""
    out, by_iid = [], {}
    for r in recs:
        if not _is_interval(r):
            out.append(r)
            continue
        m = by_iid.get(r["iid"])
        if m is None:
            by_iid[r["iid"]] = m = dict(r)
            out.append(m)
            continue
        m["n"] = max(m["n"], r["n"])
        m["first"] = min(m["first"], r["first"])
        m["last"] = max(m["last"], r["last"])
        m["ts"] = max(_ts(m), _ts(r))
    return out


def _ts(r):
    t = r.get("ts")
    return float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else 0.0


def retention_line(data, recs):
    """The retained interval: oldest..newest observation time over all rows
    read (an interval row spans first..last)."""
    times = [t for r in recs
             for t in ((r["first"], r["last"]) if _is_interval(r) else (_ts(r),)) if t]
    span = f"{_stamp(min(times))}..{_stamp(max(times))}" if times else "-"
    dropped = bool(data) and os.path.exists(
        os.path.join(data, "state", "observations.jsonl.3"))
    return (f"retained: {span} (oldest..newest observation; size-bounded, 4 files) — "
            + ("earlier records may have been rotated out (observations.jsonl.3 exists)"
               if dropped else "no generation rotated out yet")
            + "; every count below covers this interval only")


def _detail(r):
    if _is_interval(r):
        return f"x{r['n']} calls {_stamp(r['first'])}..{_stamp(r['last'])}"
    h = r.get("hook")
    if h == "gate":
        return f"{r.get('tool')} {os.path.basename(str(r.get('path') or ''))}"
    if h == "bash_guard":
        return str(r.get("target") or "")
    if h == "delegation":
        return f"{r.get('subagent_type')} model={r.get('requested_model')}"
    return ""


def _actor_calls(mine):
    actors = collections.Counter()
    for r in mine:
        if r.get("agent_type"):
            actors[r.get("agent_type")] += _calls(r)
    return actors


def observation_lines(recs, bad, sid12, pending=()):
    """`pending`: load_open_intervals rows, merged into this session's."""
    lines = []
    lines.append(
        f"observations (machine-wide, 4 files): records={len(recs)} "
        f"distinct_sids={len({r.get('sid') for r in recs})} "
        f"denies={sum(1 for r in recs if r.get('decision') == 'deny')} unparsable_lines={bad}")
    if not sid12:
        lines.append("observations (this session): session id unknown — machine-wide line only")
        return lines, []
    mine = sorted(_collapse([r for r in list(recs) + list(pending)
                             if r.get("sid") == sid12]), key=_ts)
    if not mine:
        lines.append(f"observations (this session, sid {sid12}): none")
        return lines, mine
    by_hook = collections.Counter(str(r.get("hook")) for r in mine)
    actors = _actor_calls(mine)
    lines.append(f"observations (this session, sid {sid12}): {len(mine)} records — "
                 + " ".join(f"{h}={n}" for h, n in sorted(by_hook.items()))
                 + "; subagent-origin calls: "
                 + (", ".join(f"{a}={n}" for a, n in actors.most_common()) if actors
                    else "none (no subagent origin observed — not proof worker hooks never fire)"))
    for r in mine[-LAST_N:]:
        lines.append(f"  {_stamp(r.get('ts'))} {r.get('hook')} {r.get('decision')}"
                     f"/{r.get('via') or '-'} actor={r.get('agent_type') or 'main'} {_detail(r)}".rstrip())
    return lines, mine


def delegation_lines(mine):
    dl = [r for r in mine if r.get("hook") == "delegation"]
    if not dl:
        return ["delegations (this session): none recorded"]
    rescue = [r for r in dl if str(r.get("subagent_type") or "").startswith("codex:")]
    own = [r for r in dl if not str(r.get("subagent_type") or "").startswith("codex:")]
    pairs = collections.Counter(f"{r.get('subagent_type')}/{r.get('requested_model')}" for r in own)
    pins = collections.Counter(str(r.get("tier_pin_check")) for r in own)
    return [
        f"delegations (this session): {len(own)} — " + ", ".join(
            f"{p}={n}" for p, n in pairs.most_common(LAST_N)) + " (type/requested_model; None = no override)",
        f"  denies={sum(1 for r in own if r.get('decision') == 'deny')} tier_pin_check: "
        + ", ".join(f"{p}={n}" for p, n in sorted(pins.items())),
        f"  codex: rescue delegations={len(rescue)}",
    ]


def anomaly_lines(mine):
    out, run = [], []
    for r in mine + [{}]:
        if r.get("decision") == "deny":
            run.append(r)
            continue
        if len(run) >= 3:
            out.append(f"deny streak x{len(run)} ({', '.join(sorted({str(x.get('hook')) for x in run}))})"
                       f" ending {_stamp(run[-1].get('ts'))}")
        run = []
    spawned = {r.get("subagent_type") for r in mine if r.get("hook") == "delegation"}
    for actor, n in _actor_calls(mine).most_common():
        if actor not in spawned:
            out.append(f"actor {actor} x{n} has no delegation record this session")
    if any(r.get("agent_type") for r in mine) and not spawned:
        out.append("subagent activity but NO delegation hook record this session")
    shapes = collections.Counter()
    for r in mine:
        known = KNOWN_KEYS.get(r.get("hook"))
        if r.get("v") != 2 or known is None or not set(r) <= known:
            shapes[f"hook={r.get('hook')} v={r.get('v')} keys={','.join(sorted(set(r) - (known or set())))}"] += 1
    out += [f"new shape x{n}: {s}" for s, n in shapes.most_common(5)]
    return out


def main(argv):
    data = os.path.expanduser(argv[1]) if len(argv) > 1 and argv[1] else ""
    raw_sid = argv[2] if len(argv) > 2 else ""
    sid = "" if (not raw_sid or "$" in raw_sid) else raw_sid
    now = time.time()
    on_codex = on_codex_host("", data)
    print(f"[haejwo status] data={data or '?'} host={'codex' if on_codex else 'claude'} "
          f"session={_safe_sid(sid) if sid else 'unknown'}")
    if not data or not os.path.isdir(data):
        print("data dir not found — nothing recorded yet; shipped defaults active")
    cfg, status = load_config_with_status(data)
    clines, active = config_lines(data, cfg, status, on_codex)
    print("\n".join(clines))
    budget = cfg["gate"]["max_files_per_turn"]
    print(turn_line(data, sid, "-" if budget is None else budget))
    print(reviewer_line(cfg, on_codex, now))
    pending, pnote, recs, bad, notes = read_snapshot(data)
    for n in notes + ([pnote] if pnote else []):
        print("note: " + n)
    olines, mine = observation_lines(recs, bad, sid[:12], pending)
    olines.insert(1, retention_line(data, recs + pending))
    print("\n".join(olines))
    if sid:
        print("\n".join(delegation_lines(mine)))
        an = anomaly_lines(mine)
        print("anomalies (this session): " + ("none" if not an else f"{len(an)}"))
        for a in an:
            print("  " + a)
    print(f"summary: gate {'ACTIVE' if active else 'OFF'}, "
          f"budget {'invalid' if budget is None else budget}, "
          f"configured {'yes' if cfg.get('configured') is True else 'no'}")


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Exception as e:  # exit 0 always: a status read never fails the host's turn
        print(f"note: status collection stopped early ({type(e).__name__}); the lines above are partial")
    sys.exit(0)
