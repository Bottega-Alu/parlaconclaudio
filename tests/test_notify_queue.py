"""Voice queue in notify-tts.py: several sessions announcing together get ONE
chime, then one voice per announcement in turn, help before 'has finished'.
Everything on tmp dirs; playback is recorded, never played."""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location("notify_tts_queue_under_test",
                                                  REPO / "scripts" / "notify-tts.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


nt = _load()


@pytest.fixture()
def queue(monkeypatch, tmp_path):
    played: list[str] = []
    monkeypatch.setattr(nt, "VOICE_QUEUE_DIR", tmp_path / "voice_queue")
    monkeypatch.setattr(nt, "VOICE_QUEUE_LOCK", tmp_path / "voice_queue.lock")
    monkeypatch.setattr(nt, "VOICE_LAST_END_FILE", tmp_path / "last_end.txt")
    monkeypatch.setattr(nt, "NOTIFY_BURST_DIR", tmp_path / "burst")
    monkeypatch.setattr(nt, "CHIME_GAP_MS", 0)
    monkeypatch.setattr(nt, "play_chime", lambda key, progress=0.0: played.append(f"chime:{key}"))
    monkeypatch.setattr(nt, "play_sync", lambda path, voice: played.append(path))
    monkeypatch.setattr(nt, "maybe_play_easter_egg", lambda mode: played.append(f"egg:{mode}"))
    monkeypatch.setattr(nt, "_spawn_hidden", lambda args, raw=None: False)  # drain inline
    return played


def _entry(prio, audio, chime="stop", egg=None, ts=None):
    return {"ts": time.time() if ts is None else ts, "prio": prio, "chime": chime,
            "audio": audio, "egg": egg}


def test_burst_of_projects_one_chime_then_help_first(queue):
    for e in (_entry(nt.PRIO_DONE, "alpha-finito", "stop"),
              _entry(nt.PRIO_HELP, "beta-aiuto", "permission"),
              _entry(nt.PRIO_DONE, "gamma-finito", "stop")):
        nt._write_entry(e)
    nt.drain_queue()
    assert queue == ["chime:permission", "beta-aiuto", "alpha-finito", "gamma-finito"]
    assert nt._pending_entries() == []


def test_no_second_chime_right_after_a_voice(queue):
    nt._write_entry(_entry(nt.PRIO_DONE, "alpha-finito"))
    nt.drain_queue()
    nt._write_entry(_entry(nt.PRIO_HELP, "beta-aiuto", "permission"))
    nt.drain_queue()
    assert queue == ["chime:stop", "alpha-finito", "beta-aiuto"]


def test_chime_again_after_silence(queue, monkeypatch):
    nt._write_entry(_entry(nt.PRIO_DONE, "alpha-finito"))
    nt.drain_queue()
    nt.VOICE_LAST_END_FILE.write_text(str(time.time() - nt.CHIME_QUIET_S - 1))
    nt._write_entry(_entry(nt.PRIO_DONE, "beta-finito"))
    nt.drain_queue()
    assert queue == ["chime:stop", "alpha-finito", "chime:stop", "beta-finito"]


def test_easter_egg_only_once_queue_is_empty(queue):
    nt._write_entry(_entry(nt.PRIO_DONE, "alpha-finito", egg="full"))
    nt._write_entry(_entry(nt.PRIO_DONE, "beta-finito", egg="full"))
    nt.drain_queue()
    assert queue == ["chime:stop", "alpha-finito", "beta-finito", "egg:full"]


def test_arrival_order_survives_a_coarse_clock(queue, monkeypatch):
    """Python 3.11 on Windows: time_ns ticks every ~15 ms (seen failing in CI)."""
    monkeypatch.setattr(nt.time, "time_ns", lambda: 1_000)
    for name in ("primo", "secondo", "terzo", "quarto"):
        nt._write_entry(_entry(nt.PRIO_DONE, name))
    nt.drain_queue()
    assert queue == ["chime:stop", "primo", "secondo", "terzo", "quarto"]


def test_subagent_has_no_chime_and_goes_last(queue):
    nt._write_entry(_entry(nt.PRIO_SUBAGENT, "subagente", chime=None))
    nt._write_entry(_entry(nt.PRIO_DONE, "alpha-finito"))
    nt.drain_queue()
    assert queue == ["chime:stop", "alpha-finito", "subagente"]


def test_chime_only_entry_when_voice_is_off(queue):
    nt._write_entry(_entry(nt.PRIO_HELP, None, "permission"))
    nt.drain_queue()
    assert queue == ["chime:permission"]


def test_stale_and_corrupt_entries_are_dropped(queue):
    nt._write_entry(_entry(nt.PRIO_HELP, "vecchio", ts=time.time() - nt.QUEUE_STALE_S - 5))
    nt.VOICE_QUEUE_DIR.joinpath("00000000000000000000-broken.json").write_text("{nope")
    nt._write_entry(_entry(nt.PRIO_DONE, "fresco"))
    nt.drain_queue()
    assert queue == ["chime:stop", "fresco"]
    assert nt._pending_entries() == []


def test_second_drainer_bounces_while_lock_is_held(queue):
    nt._write_entry(_entry(nt.PRIO_DONE, "alpha-finito"))
    holder = nt._QueueLock()
    assert holder.acquire()
    try:
        nt.drain_queue()  # must exit at once, entry untouched
        assert queue == [] and len(nt._pending_entries()) == 1
    finally:
        holder.release()
    nt.drain_queue()
    assert queue == ["chime:stop", "alpha-finito"]


def test_burst_is_per_session(queue):
    """A second session's hook must not swallow the first one's announcement."""
    import threading
    results = {}

    def run(sid, key):
        results[key] = nt.burst_check_should_play(200, session_id=sid)

    threads = [threading.Thread(target=run, args=("s1", "a")),
               threading.Thread(target=run, args=("s2", "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {"a": True, "b": True}


def test_burst_same_session_last_wins(queue):
    import threading
    results = {}

    def run(key, delay):
        time.sleep(delay)
        results[key] = nt.burst_check_should_play(300, session_id="s1")

    threads = [threading.Thread(target=run, args=("first", 0)),
               threading.Thread(target=run, args=("last", 0.1))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {"first": False, "last": True}


@pytest.mark.parametrize("event,sub,prio", [
    ("PreToolUse", None, 0), ("Notification", "permission_prompt", 0),
    ("Notification", "idle_prompt", 0), ("Notification", "elicitation_dialog", 0),
    ("Notification", "auth_success", 1), ("Stop", None, 1), ("TaskCompleted", None, 2)])
def test_priorities(event, sub, prio):
    assert nt.announcement_priority(event, sub) == prio


def test_main_enqueues_instead_of_playing(queue, monkeypatch):
    got = []
    monkeypatch.setattr(nt, "enqueue_announcement", got.append)
    monkeypatch.setattr(nt, "burst_check_should_play", lambda *a, **k: True)
    monkeypatch.setattr(nt, "cleanup_tts_cache", lambda: None)
    monkeypatch.setattr(nt, "load_config", lambda: {"tts_mode": "full"})
    monkeypatch.setattr(nt, "resolve_audio", lambda text, profile: "C:/x.mp3")
    monkeypatch.setattr(nt, "extract_repo_name", lambda data: "alpha")
    nt.main([], lambda: json.dumps({"hook_event_name": "Notification", "session_id": "s1",
                                    "notification_type": "permission_prompt", "cwd": "C:/x/alpha"}))
    assert len(got) == 1
    e = got[0]
    assert (e["prio"], e["chime"], e["audio"], e["egg"]) == (nt.PRIO_HELP, "permission", "C:/x.mp3", "full")
    assert queue == []  # nothing played by the hook itself
