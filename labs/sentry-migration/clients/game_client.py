#!/usr/bin/env python3
"""Game-client and mobile-app stand-in on the real Sentry Python SDK.

A Unity or Unreal client speaks the same envelope protocol through its own
SDK; this script exercises that protocol, not a game engine. Each simulated
play session starts a Sentry session, may hit handled errors, and in a share
of sessions crashes with an unhandled exception (session status `crashed`).

  uv run --python 3.12 --with sentry-sdk==3.0.0 python game_client.py \
      --dsn http://<key>@localhost:9800/102 --release 1.8.0 --sessions 60 --crash-rate 0.02
"""
import argparse
import random
import sys
import time

import sentry_sdk
from sentry_sdk.utils import event_from_exception

ap = argparse.ArgumentParser()
ap.add_argument("--dsn", required=True)
ap.add_argument("--release", required=True)
ap.add_argument("--service", default="game-client")
ap.add_argument("--platform", default="windows", choices=["windows", "android", "ios"])
ap.add_argument("--sessions", type=int, default=50)
ap.add_argument("--crash-rate", type=float, default=0.02)
ap.add_argument("--error-rate", type=float, default=0.10)
ap.add_argument("--pause", type=float, default=0.2, help="seconds between sessions")
ap.add_argument("--seed", type=int, default=7)
a = ap.parse_args()
rng = random.Random(f"{a.seed}-{a.release}-{a.platform}")

OS = {"windows": ("Windows", "10.0.22631"), "android": ("Android", "14"), "ios": ("iOS", "18.1")}
DEVICES = {"windows": ["DESKTOP-RTX4070", "DESKTOP-RTX3060", "LAPTOP-RTX4060"],
           "android": ["SM-S928N", "SM-A556N", "Pixel 9"], "ios": ["iPhone16,2", "iPhone15,4"]}

sentry_sdk.init(
    dsn=a.dsn,
    release=f"{a.service}@{a.release}",
    environment="production",
    auto_session_tracking=False,
    default_integrations=False,
)
sentry_sdk.set_tag("service", a.service)
sentry_sdk.set_context("os", {"name": OS[a.platform][0], "version": OS[a.platform][1]})


# --- the "game": a few call paths whose names show up as stack frames -----

class RenderPipeline:
    def submit(self, frame):
        material = None if frame.get("broken_material") else {"shader": "pbr"}
        return material["shader"]  # TypeError on a broken material -> crash


def load_stage(stage_id):
    if stage_id == "raid-07":
        raise KeyError(f"stage asset bundle missing: {stage_id}")
    return {"id": stage_id}


def matchmaking_request():
    raise TimeoutError("matchmaking: no response in 5000 ms")


def play_session(i):
    scope = sentry_sdk.get_isolation_scope()
    device = rng.choice(DEVICES[a.platform])
    scope.set_context("device", {"model": device})
    scope.set_user({"id": f"player-{rng.randint(1, 5000):05d}"})
    scope.start_session()
    crashed = False
    try:
        if rng.random() < a.error_rate:
            try:
                matchmaking_request()
            except TimeoutError:
                sentry_sdk.capture_exception()          # handled
        if rng.random() < a.error_rate / 2:
            try:
                load_stage("raid-07")
            except KeyError:
                sentry_sdk.capture_exception()          # handled
        if rng.random() < a.crash_rate:
            crashed = True
            RenderPipeline().submit({"broken_material": True})
    except Exception as e:
        # What an engine's crash handler does: report unhandled, mark the
        # session crashed, then the process would exit.
        event, hint = event_from_exception(
            e, mechanism={"type": "crash_handler", "handled": False}
        )
        event["level"] = "fatal"
        sentry_sdk.capture_event(event, hint=hint)
        scope._session.update(status="crashed")
    finally:
        scope.end_session()
    return crashed


crashes = 0
for i in range(a.sessions):
    crashes += play_session(i)
    time.sleep(a.pause)
sentry_sdk.flush(timeout=10)
print(f"{a.service} {a.platform} {a.release}: {a.sessions} sessions, {crashes} crashed", file=sys.stderr)
