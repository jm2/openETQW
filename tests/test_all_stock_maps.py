#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""All-stock-maps verification harness (stock-maps phase, Milestone 2+).

For every stock map a FRESH dedicated server process is started with
'+spawnServer <map>' and the following are asserted and recorded:

  a) load     'Map: maps/<m>.world', the '------------- ETQW initialized'
              ready marker, no failure markers in the whole log, exit code 0
              after 'quit'.
  b) entities '...N entities spawned, M inhibited': N+M must equal the number
              of '"classname"' lines in the retail maps/<m>.entities (read from
              base/pak*.pk4). N > 500 is the phase criterion; quarry's retail
              file only defines 415 entities, which is reported as a
              DATA_LIMITATION (owner decision) rather than a failure. Any other
              map at or below 500 fails.
  c) scripts  bot_debugMapScript 1 map-script setup prints are recorded;
              'objectiveStatus' must parse with a valid game state (rules and
              objective manager alive) at the start and end; no script runtime
              errors.
  d) AAS      "Loaded AAS file" for .aas_playerb and .aas_vehicleb with area /
              reachability counts equal to the retail files (parsed with
              test_aas_stress.parse_aas); 'AAS initialization complete: 2
              profiles loaded (player AAS: OK, vehicle AAS: OK)'; the vehicle
              .nav graph line must be present with N == retail header count > 0
              when the paks ship maps/<m>.nav and ABSENT when they do not.
  e) bots     'addbot gdf soldier' + 'addbot strogg soldier'; each gets a real
              spawn (spectating 0, right team, not the origin). playerPos is
              polled for --bot-seconds (>= 45 s). Displacement is tracked per
              life (baseline starts at the first grounded, non-spectating
              sample and resets on every new spawn line or dead->alive
              transition). Each team needs a per-life 2D displacement >= 50 u.
              A stuck metric (longest interval with fwd/side != 0 but a 2D
              speed < 10 u/s between consecutive samples) is reported. AAS area
              validity: while grounded and alive, area > 0 in >= 90 % of
              samples (needs the PLAYER_POS 'hp= dead= area=' suffix).
  f) think    'botThreadStats' at the start and end of the bot window:
              gameHz = dGameFrame / dWall must be within 27..33.5,
              thinkRatio = dBotFrames / dGameFrame is reported (and must be
              >= 0.9 with --bot-think-delay 0), waits must be 0. The server
              tick rate is cross-checked passively from SIM_HASH ticks
              (+set g_simHash 1).
  g) a summary table and an optional JSON report with per-map metrics.

Diagnostics that the build does not provide yet (botThreadStats, the
PLAYER_POS hp/dead/area suffix) are reported as UNAVAILABLE and FAIL the map;
--allow-missing-diagnostics downgrades exactly those to warnings for interim
runs. Nothing else is relaxed by that flag.

Exit codes: 0 = all maps pass, 1 = any failure, 77 = skipped (ETQW_DATA_PATH
unset / without base/pak000.pk4, or no dedicated server binary).

Extending (later milestones): the process/log plumbing and parsers live in
tests/etqw_harness.py; run_map() drives one server and then analyses the whole
log, recording each assertion with res.check(name, status, detail) and each
number in res.metrics (which lands in the JSON report). Add new stages there;
'--set CVAR VALUE' passes extra cvars to every server.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etqw_harness as H  # noqa: E402

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
UNAVAILABLE = "UNAVAILABLE"
DATA_LIMITATION = "DATA_LIMITATION"
INFO = "INFO"                   # recorded metric, not an assertion

ENTITY_CRITERION = 500          # phase criterion: more than 500 entities spawned
# Maps whose retail .entities file cannot satisfy the criterion. Reported, not
# failed, and only while the engine accounts for every retail entity. The
# owner decides how the phase treats this.
ENTITY_DATA_LIMITATION_MAPS = {"quarry"}
MIN_DISPLACEMENT = 50.0
STUCK_SPEED = 10.0
AREA_VALID_MIN_FRACTION = 0.90
GAME_HZ_RANGE = (27.0, 33.5)
THINK_RATIO_MIN = 0.9
BOT_TEAMS = ("gdf", "strogg")

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


class MapResult:
    def __init__(self, map_name: str, allow_missing: bool):
        self.map = map_name
        self.allow_missing = allow_missing
        self.checks: list[dict] = []
        self.metrics: dict = {"map": map_name}

    def check(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append({"name": name, "status": status, "detail": detail})

    def check_bool(self, name: str, ok: bool, detail: str = "") -> None:
        self.check(name, PASS if ok else FAIL, detail)

    def fatal_checks(self) -> list[dict]:
        out = []
        for c in self.checks:
            if c["status"] == FAIL or (c["status"] == UNAVAILABLE and not self.allow_missing):
                out.append(c)
        return out

    @property
    def passed(self) -> bool:
        return not self.fatal_checks()

    def to_json(self) -> dict:
        d = dict(self.metrics)
        d["status"] = PASS if self.passed else FAIL
        d["checks"] = self.checks
        return d


class BotTracker:
    """Per-client sample bookkeeping: per-life displacement, stuck time and
    AAS area validity."""

    def __init__(self, client: int):
        self.client = client
        self.team: str | None = None
        self.name: str | None = None
        self.real_spawns: list[dict] = []
        self.all_spawns: list[dict] = []
        self.lives = 0
        self.baseline: tuple[float, float] | None = None
        self.life_max = 0.0
        self.best_life_disp = 0.0
        self.best_life_index = 0
        self.prev: tuple[float, float, float] | None = None
        self.last_dead: int | None = None
        self.samples = 0
        self.alive_samples = 0
        self.grounded_alive = 0
        self.area_known = 0
        self.area_valid = 0
        self.nonfinite = 0
        self.moving_cmd_samples = 0
        self.stuck_run_start: float | None = None
        self.stuck_longest = 0.0
        self.stuck_total = 0.0
        self.max_speed = 0.0
        self.attack_seen = False
        self.suffix_seen = False
        self.deaths = 0

    def _new_life(self) -> None:
        self.lives += 1
        self.baseline = None
        self.life_max = 0.0
        self.prev = None
        self.stuck_run_start = None

    def on_spawn(self, sp: dict) -> None:
        self.all_spawns.append(sp)
        self.name = sp["name"]
        if sp["spectating"] == 0 and sp["team"] in BOT_TEAMS:
            self.team = sp["team"]
            self.real_spawns.append(sp)
            self._new_life()
            # the spawn line already started the new life; a following
            # dead=0 sample must not start another one
            if self.last_dead is not None:
                self.last_dead = 0

    def on_sample(self, s: dict, t: float) -> None:
        self.samples += 1
        x, y, z = s["org"]
        if not H.finite(x, y, z, *s["vel"]):
            self.nonfinite += 1
            self.prev = None
            self.stuck_run_start = None
            return
        if s["team"] in BOT_TEAMS:
            self.team = s["team"]
        dead = s["dead"]
        if dead is not None:
            self.suffix_seen = True
            if self.last_dead == 1 and dead == 0:
                self._new_life()
            if self.last_dead == 0 and dead == 1:
                self.deaths += 1
            self.last_dead = dead
        alive = (s["spectating"] == 0 and dead in (None, 0)
                 and (s["hp"] is None or s["hp"] > 0))
        if not alive or self.lives == 0:
            self.prev = None
            self.stuck_run_start = None
            return
        self.alive_samples += 1
        if s["atk"] or s["atk_cmd"]:
            self.attack_seen = True
        if s["ground"] == 1:
            self.grounded_alive += 1
            if s["area"] is not None:
                self.area_known += 1
                if s["area"] > 0:
                    self.area_valid += 1
        if self.baseline is None and s["ground"] == 1:
            self.baseline = (x, y)
        if self.baseline is not None:
            d = H.dist2d((x, y), self.baseline)
            self.life_max = max(self.life_max, d)
            if self.life_max > self.best_life_disp:
                self.best_life_disp = self.life_max
                self.best_life_index = self.lives
        moving = s["fwd"] != 0 or s["side"] != 0
        if moving:
            self.moving_cmd_samples += 1
        if self.prev is not None:
            pt, px, py = self.prev
            dt = t - pt
            if dt > 0:
                speed = H.dist2d((x, y), (px, py)) / dt
                self.max_speed = max(self.max_speed, speed)
                if moving and speed < STUCK_SPEED:
                    if self.stuck_run_start is None:
                        self.stuck_run_start = pt
                    self.stuck_total += dt
                    self.stuck_longest = max(self.stuck_longest, t - self.stuck_run_start)
                else:
                    self.stuck_run_start = None
        self.prev = (t, x, y)

    def area_fraction(self) -> float | None:
        if self.area_known == 0:
            return None
        return self.area_valid / self.area_known

    def to_json(self) -> dict:
        frac = self.area_fraction()
        return {
            "client": self.client, "name": self.name, "team": self.team,
            "real_spawns": [list(sp["org"]) for sp in self.real_spawns],
            "lives": self.lives, "deaths_seen": self.deaths,
            "max_life_displacement": round(self.best_life_disp, 1),
            "best_life": self.best_life_index,
            "samples": self.samples, "alive_samples": self.alive_samples,
            "grounded_alive_samples": self.grounded_alive,
            "area_samples": self.area_known, "area_valid_samples": self.area_valid,
            "area_valid_fraction": round(frac, 4) if frac is not None else None,
            "stuck_longest_s": round(self.stuck_longest, 2),
            "stuck_total_s": round(self.stuck_total, 2),
            "moving_cmd_samples": self.moving_cmd_samples,
            "max_speed_ups": round(self.max_speed, 1),
            "nonfinite_samples": self.nonfinite,
            "attack_seen": self.attack_seen,
        }


def _bot_thread_stats(server: H.DedicatedServer, timeout: float) -> tuple[str, dict | None, float | None]:
    """Run 'botThreadStats'. Returns (status, stats, monotonic time)."""
    out = server.command("botThreadStats", timeout=timeout)
    if out is None:
        return "NO_REPLY", None, None
    for ln in out:
        st = H.parse_bot_thread_stats(ln.text)
        if st is not None:
            return "OK", st, ln.t
    for ln in out:
        m = H.UNKNOWN_COMMAND_RE.search(ln.text)
        if m and m.group(1).lower() == "botthreadstats":
            return "UNKNOWN_COMMAND", None, None
    return "NO_STATS_LINE", None, None


def _objective_status(server: H.DedicatedServer, timeout: float) -> dict | None:
    out = server.command("objectiveStatus", timeout=timeout)
    for ln in out or []:
        st = H.parse_objective_status(ln.text)
        if st is not None:
            return st
    return None


def run_map(map_name: str, map_index: int, cfg: argparse.Namespace, ctx: dict) -> MapResult:
    res = MapResult(map_name, cfg.allow_missing_diagnostics)
    t_start = time.monotonic()
    paks: H.PakIndex = ctx["paks"]
    expected = ctx["expected"][map_name]
    res.metrics["retail"] = expected

    port = ctx["ports"].acquire(preferred=cfg.base_port + map_index)
    log_path = Path(cfg.log_dir) / f"{map_name}.log"
    cvars = {
        "si_rules": "sdGameRulesObjective",
        "si_pure": "0",
        "bot_enable": "1",
        "bot_sleepWhenServerEmpty": "0",
        "g_teamSwitchDelay": "0",
        "bot_debugMapScript": "1",
        # Explicit: the game default is being changed to 0; SIM_HASH ticks give
        # a passive per-frame server tick counter used as a cross-check below.
        "g_simHash": "1",
    }
    if cfg.bot_think_delay is not None:
        cvars["bot_threadMinFrameDelay"] = str(cfg.bot_think_delay)
    for name, value in cfg.extra_cvars:
        cvars[name] = value
    savepath = Path(tempfile.mkdtemp(prefix=f"save-{map_name}-", dir=ctx["tmp_root"]))
    server = H.DedicatedServer(ctx["binary"], ctx["data_path"], map_name, port,
                               log_path=log_path, cvars=cvars, savepath=savepath)
    res.metrics.update({"port": port, "log": str(log_path)})
    log(f"[{map_name}] starting on UDP {port} (log {log_path})")

    trackers: dict[int, BotTracker] = {}
    st = {"obj_start": None, "obj_end": None, "exit_code": None,
          "bts0": ("NOT_RUN", None, None), "bts1": ("NOT_RUN", None, None),
          "sim_ticks": [], "aborted": None}

    def drive() -> None:
        """Talk to the server. Any early return leaves st['aborted'] set; the
        log analysis below runs regardless so every problem is reported."""
        server.start()

        # ---------------- a) load ----------------
        ready = server.wait_for_text(H.READY_MARKER, cfg.load_timeout)
        load_s = time.monotonic() - t_start
        res.metrics["load_s"] = round(load_s, 1)
        if ready is None:
            rc = server.proc.poll()
            st["aborted"] = "load"
            res.check("load.ready_marker", FAIL,
                      f"no ready marker within {cfg.load_timeout:.0f}s "
                      f"(process {'alive' if rc is None else 'exited rc=' + str(rc)})")
            return
        res.check("load.ready_marker", PASS, f"after {load_s:.1f}s")
        if server.sync(cfg.cmd_timeout) is None:
            st["aborted"] = "sync"
            res.check("load.console_sync", FAIL, "echo SYNC did not come back")
            return

        st["obj_start"] = _objective_status(server, cfg.cmd_timeout)

        # ---------------- e) bots ----------------
        bot_mark = server.mark()
        server.send("addbot gdf soldier")
        server.send("addbot strogg soldier")
        if server.sync(cfg.cmd_timeout) is None:
            st["aborted"] = "addbot"
            res.check("bots.addbot_sync", FAIL, "echo SYNC after addbot did not come back")
            return

        # ---------------- f) think rate (start) ----------------
        st["bts0"] = _bot_thread_stats(server, cfg.cmd_timeout)

        window_start = time.monotonic()
        window_end = window_start + cfg.bot_seconds
        processed = bot_mark
        poll_clients = {0, 1}

        def pump(upto: int) -> None:
            nonlocal processed
            for ln in server.snapshot(processed, upto):
                sp = H.parse_spawn(ln.text)
                if sp is not None:
                    trackers.setdefault(sp["client"], BotTracker(sp["client"])).on_spawn(sp)
                    poll_clients.add(sp["client"])
                    continue
                if ln.text.startswith("PLAYER_POS:"):
                    s = H.parse_player_pos(ln.text)
                    if s is not None:
                        trackers.setdefault(s["client"], BotTracker(s["client"])).on_sample(s, ln.t)
                    continue
                m = H.SIM_HASH_TICK_RE.search(ln.text)
                if m and window_start <= ln.t:
                    st["sim_ticks"].append((ln.t, int(m.group(1))))
            processed = upto

        while time.monotonic() < window_end:
            if not server.alive():
                break
            for c in sorted(poll_clients):
                server.send(f"playerPos {c}")
            idx = server.sync(cfg.cmd_timeout)
            if idx is None:
                break
            pump(idx + 1)
            time.sleep(cfg.poll_interval)
        pump(server.mark())
        window_s = time.monotonic() - window_start
        res.metrics["bot_window_s"] = round(window_s, 1)
        if window_s < cfg.bot_seconds - 0.5:
            st["aborted"] = "bot_window"
            res.check("bots.window", FAIL,
                      f"bot window ended after {window_s:.1f}s < {cfg.bot_seconds:.0f}s (server alive={server.alive()})")
        else:
            res.check("bots.window", PASS, f"{window_s:.1f}s of playerPos polling")

        # ---------------- f) think rate (end) ----------------
        st["bts1"] = _bot_thread_stats(server, cfg.cmd_timeout)
        st["obj_end"] = _objective_status(server, cfg.cmd_timeout)

        # ---------------- quit ----------------
        st["exit_code"] = server.quit(cfg.quit_timeout)

    try:
        drive()
    finally:
        if st["exit_code"] is None:
            server.kill()
        lines = [ln.text for ln in server.snapshot()]
        server.cleanup()
        ctx["ports"].release(port)
        res.metrics["run_s"] = round(time.monotonic() - t_start, 1)
        res.metrics["exit_code"] = st["exit_code"]
        res.metrics["aborted"] = st["aborted"]

    exit_code = st["exit_code"]
    obj_start, obj_end = st["obj_start"], st["obj_end"]
    bts0_status, bts0, bts0_t = st["bts0"]
    bts1_status, bts1, bts1_t = st["bts1"]
    sim_ticks = st["sim_ticks"]

    # =====================================================================
    # Analysis of the complete log
    # =====================================================================
    if st["aborted"] and exit_code is None:
        res.check("load.exit_code", FAIL, f"run aborted at stage '{st['aborted']}'; server killed, no clean 'quit'")
    else:
        res.check_bool("load.exit_code", exit_code == 0,
                       f"exit code {exit_code} after 'quit'" if exit_code is not None
                       else "server did not exit within --quit-timeout after 'quit' (killed)")

    map_lines = [H.MAP_LINE_RE.match(s).group(1) for s in lines if H.MAP_LINE_RE.match(s)]
    want_map = f"maps/{map_name}.world"
    res.check_bool("load.map_line", want_map in map_lines,
                   f"'Map: {want_map}' {'seen' if want_map in map_lines else 'missing'} (map lines: {map_lines})")

    failures = H.scan_failures(lines)
    res.metrics["failure_markers"] = failures[:20]
    non_script = [f for f in failures if f["kind"] != "script"]
    res.check_bool("load.failure_markers", not non_script,
                   "none" if not non_script else
                   "; ".join(f"line {f['line']}: {f['text'][:160]}" for f in non_script[:5]))

    # ---------------- b) entities ----------------
    ent = [H.ENTITIES_SPAWNED_RE.match(s) for s in lines]
    ent = [m for m in ent if m]
    retail_total = expected["entities"]
    if not ent:
        res.check("entities.spawned_line", FAIL, "no '...N entities spawned, M inhibited' line")
    else:
        # A map restart (e.g. at the end of warmup) legitimately spawns the
        # map again; every pass must account for all retail entities.
        passes = [(int(m.group(1)), int(m.group(2))) for m in ent]
        n, m_inh = passes[0]
        res.metrics["entities"] = {"spawned": n, "inhibited": m_inh, "total": n + m_inh,
                                   "retail_classnames": retail_total, "over_500": n > ENTITY_CRITERION,
                                   "spawn_passes": len(passes)}
        bad = [p for p in passes if retail_total is None or sum(p) != retail_total]
        res.check_bool("entities.match_retail", not bad,
                       f"{n} spawned + {m_inh} inhibited = {n + m_inh}; retail maps/{map_name}.entities has {retail_total}"
                       + (f" ({len(passes)} spawn passes)" if len(passes) > 1 else "")
                       + (f"; mismatching passes {bad}" if bad and len(passes) > 1 else ""))
        if n > ENTITY_CRITERION:
            res.check("entities.over_500", PASS, f"{n} > {ENTITY_CRITERION}")
        elif (map_name in ENTITY_DATA_LIMITATION_MAPS and retail_total is not None
              and retail_total <= ENTITY_CRITERION and n + m_inh == retail_total):
            res.check("entities.over_500", DATA_LIMITATION,
                      f"{n} spawned <= {ENTITY_CRITERION}: the retail maps/{map_name}.entities defines only "
                      f"{retail_total} entities (all accounted for); documented data limitation, owner decision")
        else:
            res.check("entities.over_500", FAIL, f"{n} spawned <= {ENTITY_CRITERION}")

    # ---------------- c) scripts ----------------
    debug_prints = [H.SCRIPT_DEBUG_RE.match(s).group(1) for s in lines if H.SCRIPT_DEBUG_RE.match(s)]
    script_errors = [f for f in failures if f["kind"] == "script"]
    res.metrics["script"] = {
        "debug_prints": debug_prints,
        "objective_start": obj_start,
        "objective_end": obj_end,
        "script_errors": [f["text"] for f in script_errors[:10]],
    }
    for label, st in (("start", obj_start), ("end", obj_end)):
        res.check_bool(f"script.objective_status_{label}", st is not None and st["state"] >= 0,
                       f"OBJECTIVE_STATUS {st}" if st else "no OBJECTIVE_STATUS reply")
    res.check_bool("script.runtime_errors", not script_errors,
                   "none" if not script_errors else "; ".join(f["text"][:160] for f in script_errors[:5]))
    # Recorded only: the retail island/quarry/volcano scripts print nothing
    # at map start even with bot_debugMapScript 1 (their debug prints sit in
    # later objective callbacks), so absence is not a failure.
    res.check("script.bot_setup_prints", INFO,
              f"{len(debug_prints)} bot_debugMapScript print(s)" +
              (f": {debug_prints}" if debug_prints else ""))

    # ---------------- d) AAS / nav ----------------
    aas_seen: dict[str, tuple[int, int]] = {}
    for s in lines:
        m = H.AAS_LOADED_RE.search(s)
        if m and m.group(1) == map_name:
            aas_seen[m.group(2)] = (int(m.group(3)), int(m.group(4)))
    res.metrics["aas"] = {}
    for ext in ("aas_playerb", "aas_vehicleb"):
        exp = expected[ext]
        got = aas_seen.get(ext)
        res.metrics["aas"][ext] = {"engine": list(got) if got else None, "retail": list(exp) if exp else None}
        if got is None:
            res.check(f"aas.{ext}", FAIL, f"no \"Loaded AAS file: 'maps/{map_name}.{ext}'\" line")
        else:
            ok = got[0] > 0 and exp is not None and tuple(got) == tuple(exp)
            res.check_bool(f"aas.{ext}", ok, f"engine {got[0]} areas/{got[1]} reach; retail {exp}")
    init = [H.AAS_INIT_RE.search(s) for s in lines]
    init = [m for m in init if m]
    if init:
        m = init[-1]
        ok = m.group(1) == "2" and m.group(2) == "OK" and m.group(3) == "OK"
        res.metrics["aas"]["init"] = m.group(0)
        res.check_bool("aas.init_complete", ok, m.group(0))
    else:
        res.check("aas.init_complete", FAIL, "no 'AAS initialization complete' line")

    nav_lines = [H.NAV_LOADED_RE.search(s) for s in lines]
    nav_lines = [m for m in nav_lines if m and m.group(1) == map_name]
    nav_hdr = expected["nav"]
    nav_nodes = int(nav_lines[-1].group(2)) if nav_lines else None
    res.metrics["nav"] = {"engine_nodes": nav_nodes,
                          "retail_present": nav_hdr is not None,
                          "retail_header_nodes": nav_hdr[1] if nav_hdr else None}
    if nav_hdr is not None:
        ok = nav_nodes is not None and nav_nodes > 0 and nav_nodes == nav_hdr[1]
        res.check_bool("nav.graph", ok,
                       f"engine {nav_nodes} nodes; retail maps/{map_name}.nav header {nav_hdr[1]} nodes (version {nav_hdr[0]})")
    else:
        res.check_bool("nav.graph_absent", nav_nodes is None,
                       f"no maps/{map_name}.nav in any pak; engine line {'absent' if nav_nodes is None else 'PRESENT (' + str(nav_nodes) + ' nodes)'}")

    # ---------------- e) bots ----------------
    by_team: dict[str, list[BotTracker]] = {t: [] for t in BOT_TEAMS}
    for tr in trackers.values():
        if tr.team in by_team and (tr.real_spawns or tr.samples):
            by_team[tr.team].append(tr)
    res.metrics["bots"] = {str(c): tr.to_json() for c, tr in sorted(trackers.items())}
    suffix_available = any(tr.suffix_seen for tr in trackers.values())
    for team in BOT_TEAMS:
        trs = by_team[team]
        spawns = [sp for tr in trs for sp in tr.real_spawns]
        good = [sp for sp in spawns if H.finite(*sp["org"]) and sp["org"] != (0.0, 0.0, 0.0)]
        bad = [sp for sp in spawns if sp not in good]
        res.check_bool(f"bots.{team}.real_spawn", bool(good) and not bad,
                       f"{len(spawns)} spawn(s) with spectating 0 team {team}" +
                       (f", first at {good[0]['org']}" if good else "") +
                       (f"; INVALID spawn origins {[sp['org'] for sp in bad]}" if bad else ""))
        best = max((tr.best_life_disp for tr in trs), default=0.0)
        lives = sum(tr.lives for tr in trs)
        res.check_bool(f"bots.{team}.displacement", best >= MIN_DISPLACEMENT,
                       f"max per-life 2D displacement {best:.1f}u (>= {MIN_DISPLACEMENT:.0f}u) over {lives} life/lives")
        nonfinite = sum(tr.nonfinite for tr in trs)
        res.check_bool(f"bots.{team}.finite_state", nonfinite == 0, f"{nonfinite} non-finite PLAYER_POS samples")
        stuck = max((tr.stuck_longest for tr in trs), default=0.0)
        res.check(f"bots.{team}.stuck_longest", INFO,
                  f"{stuck:.1f}s longest with fwd/side != 0 and 2D speed < {STUCK_SPEED:.0f}u/s between samples")
        if not suffix_available:
            res.check(f"bots.{team}.aas_area_validity", UNAVAILABLE,
                      "PLAYER_POS has no 'hp= dead= area=' suffix in this build")
        else:
            known = sum(tr.area_known for tr in trs)
            valid = sum(tr.area_valid for tr in trs)
            if known == 0:
                res.check(f"bots.{team}.aas_area_validity", FAIL, "no grounded+alive samples with an area value")
            else:
                frac = valid / known
                res.check_bool(f"bots.{team}.aas_area_validity", frac >= AREA_VALID_MIN_FRACTION,
                               f"area>0 in {valid}/{known} grounded+alive samples ({frac:.1%}, need >= {AREA_VALID_MIN_FRACTION:.0%})")

    # ---------------- f) think rate ----------------
    think: dict = {"start": bts0, "end": bts1, "start_status": bts0_status, "end_status": bts1_status}
    if bts0 is not None and bts1 is not None and bts1_t and bts0_t and bts1_t > bts0_t:
        dwall = bts1_t - bts0_t
        dframe = bts1["gameFrame"] - bts0["gameFrame"]
        dbot = bts1["botFrames"] - bts0["botFrames"]
        game_hz = dframe / dwall
        ratio = dbot / dframe if dframe > 0 else 0.0
        think.update({"wall_s": round(dwall, 2), "game_frames": dframe, "bot_frames": dbot,
                      "game_hz": round(game_hz, 2), "think_ratio": round(ratio, 3),
                      "game_time_ratio": round((bts1["gameTimeMs"] - bts0["gameTimeMs"]) / 1000.0 / dwall, 3)})
        res.check_bool("think.game_hz", GAME_HZ_RANGE[0] <= game_hz <= GAME_HZ_RANGE[1],
                       f"{dframe} game frames in {dwall:.1f}s = {game_hz:.2f} Hz (need {GAME_HZ_RANGE[0]}..{GAME_HZ_RANGE[1]})")
        detail = (f"{dbot} bot frames / {dframe} game frames = {ratio:.3f} "
                  f"(threading={bts1['threading']} running={bts1['running']} minDelay={bts1['minDelay']} maxDelay={bts1['maxDelay']})")
        if cfg.bot_think_delay == 0:
            res.check_bool("think.ratio", ratio >= THINK_RATIO_MIN, detail + f", need >= {THINK_RATIO_MIN}")
        else:
            res.check("think.ratio", INFO, detail + " (asserted only with --bot-think-delay 0)")
        res.check_bool("think.waits", bts1["waits"] == 0, f"waits={bts1['waits']} at end of window")
    else:
        why = f"botThreadStats start={bts0_status} end={bts1_status}"
        status = UNAVAILABLE if "UNKNOWN_COMMAND" in (bts0_status, bts1_status) else FAIL
        for name in ("think.game_hz", "think.ratio", "think.waits"):
            res.check(name, status, why)
    if len(sim_ticks) >= 2 and sim_ticks[-1][0] > sim_ticks[0][0]:
        (t0, k0), (t1, k1) = sim_ticks[0], sim_ticks[-1]
        sim_hz = (k1 - k0) / (t1 - t0)
        think["sim_hash_hz"] = round(sim_hz, 2)
        think["sim_hash_ticks"] = k1 - k0
        res.check_bool("think.sim_hash_tick_rate", GAME_HZ_RANGE[0] <= sim_hz <= GAME_HZ_RANGE[1],
                       f"SIM_HASH ticks {k1 - k0} in {t1 - t0:.1f}s = {sim_hz:.2f} Hz (passive cross-check)")
    else:
        think["sim_hash_hz"] = None
        res.check("think.sim_hash_tick_rate", WARN, "no SIM_HASH lines during the bot window (g_simHash 1 was set)")
    res.metrics["think"] = think
    return res


# =========================================================================
# Reporting
# =========================================================================

def _fmt(v, spec="") -> str:
    if v is None:
        return "-"
    return format(v, spec) if spec else str(v)


def summary_table(results: list[MapResult]) -> str:
    hdr = ["map", "result", "ents N+M/retail", ">500", "player AAS", "vehicle AAS", "nav",
           "obj state", "GDF disp", "STR disp", "stuck G/S s", "area% G/S", "gameHz", "think", "simHz", "time s"]
    rows = [hdr]
    for r in results:
        m = r.metrics
        e = m.get("entities") or {}
        ents = f"{e.get('spawned', '-')}+{e.get('inhibited', '-')}/{m.get('retail', {}).get('entities', '-')}"
        over = next((c["status"] for c in r.checks if c["name"] == "entities.over_500"), "-")
        over = {"PASS": "yes", "FAIL": "NO", "DATA_LIMITATION": "DATA_LIM"}.get(over, over)
        aas = m.get("aas", {})

        def aas_s(ext):
            v = (aas.get(ext) or {}).get("engine")
            return f"{v[0]}/{v[1]}" if v else "MISSING"
        nav = m.get("nav", {})
        nav_s = (str(nav.get("engine_nodes")) if nav.get("retail_present")
                 else ("none(ok)" if nav.get("engine_nodes") is None else "UNEXPECTED")) if nav else "-"
        sc = m.get("script", {})
        obj = sc.get("objective_end") or sc.get("objective_start")
        obj_s = str(obj["state"]) if obj else "-"
        bots = m.get("bots", {})

        def team_val(team, key, spec):
            vals = [b[key] for b in bots.values() if b.get("team") == team and b.get(key) is not None]
            return format(max(vals), spec) if vals else "-"
        th = m.get("think", {})
        rows.append([
            r.map, "PASS" if r.passed else "FAIL", ents, over,
            aas_s("aas_playerb"), aas_s("aas_vehicleb"), nav_s, obj_s,
            team_val("gdf", "max_life_displacement", ".0f"), team_val("strogg", "max_life_displacement", ".0f"),
            f"{team_val('gdf', 'stuck_longest_s', '.1f')}/{team_val('strogg', 'stuck_longest_s', '.1f')}",
            f"{team_val('gdf', 'area_valid_fraction', '.0%')}/{team_val('strogg', 'area_valid_fraction', '.0%')}",
            _fmt(th.get("game_hz"), ".2f"), _fmt(th.get("think_ratio"), ".3f"), _fmt(th.get("sim_hash_hz"), ".2f"),
            _fmt(m.get("run_s"), ".0f"),
        ])
    widths = [max(len(str(row[i])) for row in rows) for i in range(len(hdr))]
    out = []
    for i, row in enumerate(rows):
        out.append(" | ".join(str(c).ljust(widths[j]) for j, c in enumerate(row)))
        if i == 0:
            out.append("-+-".join("-" * w for w in widths))
    return "\n".join(out)


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--maps", nargs="+", default=None,
                    help="subset of stock maps (space or comma separated); default: all 11")
    ap.add_argument("--jobs", type=int, default=1, help="maps to run concurrently (distinct ports)")
    ap.add_argument("--json", dest="json_path", default=None, help="write a JSON report here")
    ap.add_argument("--log-dir", default=None, help="directory for per-map server logs (default: a new temp dir)")
    ap.add_argument("--ded-binary", default=None, help="dedicated server binary (default: $OPENETQW_DED_BINARY or build/openETQW-ded_<arch>)")
    ap.add_argument("--bot-think-delay", type=int, default=None,
                    help="set bot_threadMinFrameDelay; with 0 the think ratio must be >= 0.9 (true 30 Hz AI)")
    ap.add_argument("--bot-seconds", type=float, default=45.0, help="playerPos polling window per map (>= 45)")
    ap.add_argument("--poll-interval", type=float, default=0.5, help="seconds between playerPos polls")
    ap.add_argument("--base-port", type=int, default=int(os.environ.get("OPENETQW_TEST_BASE_PORT", "27900")),
                    help="first UDP port; map i prefers base+i (default 27900 or $OPENETQW_TEST_BASE_PORT)")
    ap.add_argument("--port-span", type=int, default=100, help="size of the UDP port range to allocate from")
    ap.add_argument("--load-timeout", type=float, default=180.0)
    ap.add_argument("--cmd-timeout", type=float, default=20.0)
    ap.add_argument("--quit-timeout", type=float, default=45.0)
    ap.add_argument("--set", dest="extra_cvars", nargs=2, action="append", default=[], metavar=("CVAR", "VALUE"),
                    help="extra '+set CVAR VALUE' for every server (repeatable; later milestones' knobs)")
    ap.add_argument("--allow-missing-diagnostics", action="store_true",
                    help="report missing botThreadStats / PLAYER_POS hp,dead,area as warnings instead of failures")
    ap.add_argument("--allow-short-window", action="store_true", help=argparse.SUPPRESS)
    cfg = ap.parse_args(argv)
    if cfg.bot_seconds < 45.0 and not cfg.allow_short_window:
        ap.error("--bot-seconds must be >= 45 (the phase requires >= 45 s of bot traversal)")
    maps: list[str] = []
    for item in cfg.maps or list(H.STOCK_MAPS):
        maps += [s for s in item.replace(",", " ").split() if s]
    unknown = [m for m in maps if m not in H.STOCK_MAPS]
    if unknown:
        ap.error(f"unknown map(s) {unknown}; stock maps are {', '.join(H.STOCK_MAPS)}")
    cfg.map_list = list(dict.fromkeys(maps))
    cfg.jobs = max(1, cfg.jobs)
    return cfg


def main(argv: list[str]) -> int:
    H.install_signal_handlers()
    cfg = parse_args(argv)
    print("=" * 78)
    print(" OpenETQW all-stock-maps verification (load / entities / scripts / AAS / bots / think rate)")
    print("=" * 78, flush=True)
    try:
        data_path = H.resolve_data_path()
        binary = H.find_ded_binary(cfg.ded_binary)
    except H.SkipTest as e:
        print(f"[SKIP] {e}", flush=True)
        return H.EXIT_SKIP

    t_all = time.monotonic()
    paks = H.PakIndex(data_path)
    expected: dict[str, dict] = {}
    for m in cfg.map_list:
        expected[m] = {
            "entities": H.retail_entity_count(paks, m),
            "aas_playerb": H.retail_aas_counts(paks, m, "aas_playerb"),
            "aas_vehicleb": H.retail_aas_counts(paks, m, "aas_vehicleb"),
            "nav": H.retail_nav_header(paks, m),
        }
    tmp_root = Path(tempfile.mkdtemp(prefix="openetqw-stockmaps-"))
    keep_tmp_root = cfg.log_dir is None  # logs live inside it then
    cfg.log_dir = cfg.log_dir or str(tmp_root / "logs")
    Path(cfg.log_dir).mkdir(parents=True, exist_ok=True)
    print(f" binary   : {binary}")
    print(f" data     : {data_path} ({', '.join(p.name for p in paks.paks)})")
    print(f" maps     : {' '.join(cfg.map_list)}  (jobs={cfg.jobs}, bot window={cfg.bot_seconds:.0f}s, "
          f"bot_threadMinFrameDelay={'default' if cfg.bot_think_delay is None else cfg.bot_think_delay})")
    print(f" logs     : {cfg.log_dir}")
    if cfg.extra_cvars:
        print(f" extra    : {' '.join('+set %s %s' % (k, v) for k, v in cfg.extra_cvars)}")
    if cfg.allow_missing_diagnostics:
        print(" NOTE     : --allow-missing-diagnostics: UNAVAILABLE diagnostics are warnings, not failures")
    print(flush=True)

    ctx = {"paks": paks, "expected": expected, "binary": binary, "data_path": data_path,
           "ports": H.PortAllocator(cfg.base_port, cfg.port_span), "tmp_root": str(tmp_root)}
    results: dict[str, MapResult] = {}

    def job(i_map):
        i, m = i_map
        try:
            r = run_map(m, H.STOCK_MAPS.index(m), cfg, ctx)
        except Exception as e:  # harness bug or unexpected engine behaviour: visible failure
            import traceback
            r = MapResult(m, cfg.allow_missing_diagnostics)
            r.check("harness.exception", FAIL, f"{type(e).__name__}: {e}")
            log(traceback.format_exc())
        fatal = r.fatal_checks()
        log(f"[{m}] {'PASS' if r.passed else 'FAIL'} in {r.metrics.get('run_s', '?')}s"
            + ("" if r.passed else ": " + "; ".join(f"{c['name']} {c['status']}: {c['detail']}" for c in fatal)))
        return m, r

    with concurrent.futures.ThreadPoolExecutor(max_workers=cfg.jobs) as ex:
        for m, r in ex.map(job, list(enumerate(cfg.map_list))):
            results[m] = r

    ordered = [results[m] for m in cfg.map_list]
    print()
    print("=" * 78)
    print(" Per-map checks")
    print("=" * 78)
    for r in ordered:
        print(f"\n[{r.map}] {'PASS' if r.passed else 'FAIL'}  (log: {r.metrics.get('log')})")
        for c in r.checks:
            status = c["status"]
            if status == UNAVAILABLE and cfg.allow_missing_diagnostics:
                status = "UNAVAILABLE(warn)"
            print(f"  {status:<17} {c['name']:<32} {c['detail']}")
    print()
    print("=" * 78)
    print(" Summary")
    print("=" * 78)
    print(summary_table(ordered))
    n_fail = sum(1 for r in ordered if not r.passed)
    limits = [r.map for r in ordered if any(c["status"] == DATA_LIMITATION for c in r.checks)]
    unavailable = sorted({c["name"].split(".")[0] + "." + c["name"].split(".")[-1]
                          for r in ordered for c in r.checks if c["status"] == UNAVAILABLE})
    total_s = time.monotonic() - t_all
    print()
    if limits:
        print(f" DATA_LIMITATION (not counted as failure; owner decision): {', '.join(limits)} "
              f"(retail .entities defines <= {ENTITY_CRITERION} entities)")
    if unavailable:
        print(f" UNAVAILABLE diagnostics ({'warnings' if cfg.allow_missing_diagnostics else 'FAILURES'}): {', '.join(unavailable)}")
    print(f" {len(ordered) - n_fail}/{len(ordered)} maps passed in {total_s:.0f}s")

    if cfg.json_path:
        report = {
            "binary": str(binary), "data_path": str(data_path),
            "jobs": cfg.jobs, "bot_seconds": cfg.bot_seconds, "bot_think_delay": cfg.bot_think_delay,
            "allow_missing_diagnostics": cfg.allow_missing_diagnostics,
            "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "total_s": round(total_s, 1),
            "passed": n_fail == 0,
            "maps": {r.map: r.to_json() for r in ordered},
        }
        Path(cfg.json_path).parent.mkdir(parents=True, exist_ok=True)
        with open(cfg.json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, default=str)
        print(f" JSON report: {cfg.json_path}")
    if not keep_tmp_root:
        shutil.rmtree(tmp_root, ignore_errors=True)  # only the per-server fs_savepath dirs
    return H.EXIT_PASS if n_fail == 0 else H.EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
