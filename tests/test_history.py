"""Every transcription is kept (newest 100), with its outcome, and can be cleared."""
from src.voice_bridge import history


def test_add_keeps_newest_and_outcome(tmp_path, monkeypatch):
    path = tmp_path / "h.jsonl"
    monkeypatch.setattr(history, "MAX_ENTRIES", 3)
    for i in range(5):
        history.add(f"testo {i}", "pasted", path)
    history.add("   ", "pasted", path)        # nothing said: not recorded
    history.add("non incollato", "weird", path)
    saved = history.entries(path)
    assert [e["text"] for e in saved] == ["testo 3", "testo 4", "non incollato"]
    assert saved[-1]["outcome"] == "failed" and saved[0]["ts"]
    path.write_text(path.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    assert len(history.entries(path)) == 3
    history.clear(path)
    assert history.entries(path) == []


def test_history_can_be_switched_off_and_switching_off_erases_it(tmp_path, monkeypatch):
    from src.voice_bridge import tray_icon
    path = history.history_file()
    assert path.parent == tmp_path / "localappdata" / "ParlaConClaudio"  # conftest: never the real one
    history.add("segreto", "pasted")
    assert history.keep({}) is True
    tray = tray_icon.TrayIcon()
    tray._set_keep_history(False)
    assert tray_icon._load_config()["keep_history"] is False
    assert history.keep(tray_icon._load_config()) is False
    assert history.entries() == [] and not path.exists()
    tray._set_keep_history(True)
    assert history.keep(tray_icon._load_config()) is True
