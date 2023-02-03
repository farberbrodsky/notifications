import time
import requests
from os import environ
from pathlib import Path
from typing import List, Callable
from collections import namedtuple
import subprocess
import json

def telegram_setup() -> Callable:
    TELEGRAM_TOKEN = environ["TELEGRAM_TOKEN"]
    if len(TELEGRAM_TOKEN) == 0 or len(TELEGRAM_TOKEN.split(":")) != 2:
        print("No telegram token :(")
        exit(1)

    TELEGRAM_CHAT_ID = environ["TELEGRAM_CHAT_ID"]
    try:
        TELEGRAM_CHAT_ID = int(TELEGRAM_CHAT_ID)
        if TELEGRAM_CHAT_ID == 0:
            raise Exception()
    except:
        print("Invalid telegram chat id")
        exit(1)

    def telegram_notify(msg):
        requests.post("https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage", data = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg
        })

    return telegram_notify

def test_setup() -> Callable:
    def test_notify(msg):
        print("NOTIFY:", msg)

    return test_notify

send_notification = None
BACKEND=environ["BACKEND"]

if BACKEND == "TELEGRAM":
    send_notification = telegram_setup()
elif BACKEND == "TEST":
    send_notification = test_setup()
else:
    print("Please select a backend")
    exit(1)

ScriptManifest = namedtuple("ScriptManifest", ["interval", "last_run", "only_if_changed"])

"""
Gets the outputs of a script and outputs (success, notification, manifest)
"""
def handle_script_outputs(exitcode, out: List[str], err: str) -> tuple[bool, str, ScriptManifest]:
    if len(err) != 0 or exitcode != 0 or len(out) == 0:
        return (False, f"Failed ({exitcode}): stdout {out}, stderr {err}", None)

    manifest_line = out[0]
    try:
        manifest_line = json.loads(manifest_line)
        manifest_line["interval"]
        manifest_line["only_if_changed"]
        assert(isinstance(manifest_line["interval"], int))
        assert(isinstance(manifest_line["only_if_changed"], bool))
        assert(manifest_line["interval"] > 0)
    except:
        return (False, "Invalid manifest", None)

    manifest = ScriptManifest(interval=manifest_line["interval"], last_run=time.time(), only_if_changed=manifest_line["only_if_changed"])
    return (True, "\n".join(out[1:]), manifest)

"""
Gets all notifications, except for scripts that are marked as sleeping because of their interval.
Outputs (filename -> successful scripts, set of failing scripts filenames)
old_outputs is changed
"""
def get_all_notifications(sleeping: set[Path], known_failing: set[Path], old_outputs: dict[Path, str]) -> tuple[dict[Path, ScriptManifest], set[Path]]:
    successful_script_info = {}
    failing_script_info = set()

    for file in Path("./scripts").iterdir():
        if not file.is_file():
            continue
        if file in sleeping:
            continue

        print("running:", file)

        proc = subprocess.Popen([file.parts[-1]], executable="./" + file.parts[-1], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=Path("./scripts"))
        out, err = proc.communicate(timeout=5000)
        exitcode = proc.returncode
        print(out, err)

        print("finished running:", file)

        # decode from utf-8
        try:
            out = out.decode("utf-8").strip().split("\n")
        except:
            out = []
            exitcode = 1337
        try:
            err = err.decode("utf-8")
        except:
            err = ""

        success, notification, manifest = False, "", None
        try:
            success, notification, manifest = handle_script_outputs(exitcode, out, err)
        except Exception as e:
            notification = f"Exception: {e}"
            pass

        # only output notification about first-time failed scripts
        if success or (file not in known_failing):
            # don't output for only-if-changed if not changed
            if not ((manifest is not None) and (manifest.only_if_changed) and (file in old_outputs) and (old_outputs[file] == notification)):
                send_notification(notification)

            if (manifest is not None) and (manifest.only_if_changed):
                old_outputs[file] = notification

        # mark script as successful or failing
        if success:
            successful_script_info[file] = manifest
        else:
            failing_script_info.add(file)


    return successful_script_info, failing_script_info


# main loop
sleeping: dict[Path, ScriptManifest] = {}
known_failing: set[Path] = set()
old_outputs = {}

while True:
    # remove from sleeping based on time
    next_sleeping = {}
    next_ready_time = (1 << 63)
    t = time.time()
    for p, manifest in sleeping.items():
        next_ready_time = min(next_ready_time, manifest.last_run + manifest.interval)
        if t < (manifest.last_run + manifest.interval):
            # don't remove
            next_sleeping[p] = manifest
    sleeping = next_sleeping

    if (t < next_ready_time) and next_ready_time != (1 << 63):
        sleep_time = 1 + next_ready_time - t
        time.sleep(min(60, sleep_time))  # sleep up to 60 seconds at a time
        continue
    else:
        time.sleep(10)  # minimum sleep without scripts

    print("DEBUG", time.time(), "sleeping", sleeping, "failing", known_failing, "old", old_outputs)
    successful, failing = get_all_notifications(sleeping.keys(), known_failing, old_outputs)
    known_failing = failing
    print("DEBUG", time.time(), "successful", successful, "failing", failing, "old", old_outputs)

    for p, manifest in successful.items():
        sleeping[p] = manifest

