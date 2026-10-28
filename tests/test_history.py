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
