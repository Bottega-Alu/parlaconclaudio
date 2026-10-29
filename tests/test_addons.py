"""Sound packs as add-ons: catalogue from a GitHub release, verified download, safe extract,
atomic replace, daily auto-update of installed packs only. Network is a fake `fetch`."""
import hashlib
import io
import json
import zipfile

import pytest

from src.voice_bridge import addons


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def entry(name, blob, **extra):
    return {"name": name, "file": f"{name}.zip", "sha256": hashlib.sha256(blob).hexdigest(),
            "size": len(blob), "sounds": 1, **extra}


class FakeNet:
    def __init__(self, packs):
        self.blobs = {f"{name}.zip": blob for name, blob in packs.items()}
        self.manifest = {"format": 1, "packs": [entry(name, blob) for name, blob in packs.items()]}
        self.calls = []

    def __call__(self, url, limit):
        self.calls.append(url)
        if url == addons.MANIFEST_URL:
            return json.dumps(self.manifest).encode()
        name = url.rsplit("/", 1)[-1]
        assert url == addons.RELEASE_URL + name
        return self.blobs[name]


@pytest.fixture
def sounds(tmp_path, monkeypatch):
    monkeypatch.setattr(addons, "SOUNDS_DIR", tmp_path / "sounds")
    monkeypatch.setattr(addons, "STATE_FILE", tmp_path / "addons-state.json")
    return tmp_path / "sounds"


DUNE = make_zip({"manifest.json": '{"pack": "dune"}', "spice.mp3": b"ID3 audio"})


def test_install_verifies_and_extracts_into_the_sounds_folder(sounds):
    net = FakeNet({"dune": DUNE})
    catalog = addons.fetch_catalog(fetch=net)
    assert [p.name for p in catalog] == ["dune"]
    addons.install(catalog[0], fetch=net)
    assert (sounds / "dune" / "spice.mp3").read_bytes() == b"ID3 audio"
    assert addons.installed() == {"dune": catalog[0].sha256}
    assert not [p for p in sounds.iterdir() if p.name.startswith("_")]  # no leftovers


def test_a_tampered_download_is_rejected_and_nothing_changes(sounds):
    net = FakeNet({"dune": DUNE})
    catalog = addons.fetch_catalog(fetch=net)
    net.blobs["dune.zip"] = make_zip({"spice.mp3": b"something else"})
    with pytest.raises(addons.AddonError) as err:
        addons.install(catalog[0], fetch=net)
    assert err.value.kind == "integrity"
    assert not sounds.exists() or not any(sounds.iterdir())


@pytest.mark.parametrize("bad", ["../evil.mp3", "/abs.mp3", "C:/x.mp3", "sub/../../x.mp3", "run.exe"])
def test_zip_entries_outside_the_pack_or_not_audio_are_refused(sounds, bad):
    blob = make_zip({"ok.mp3": b"a", bad: b"x"})
    net = FakeNet({"dune": blob})
    with pytest.raises(addons.AddonError) as err:
        addons.install(addons.fetch_catalog(fetch=net)[0], fetch=net)
    assert err.value.kind == "unsafe"
    assert not (sounds.parent / "evil.mp3").exists()


def test_invalid_catalogue_entries_are_skipped_not_trusted(sounds):
    net = FakeNet({"dune": DUNE})
    net.manifest["packs"] += [{"name": "../etc", "file": "x.zip", "sha256": "0" * 64, "size": 1},
                              {"name": "ok-name", "file": "../x.zip", "sha256": "0" * 64, "size": 1},
                              {"name": "nohash", "file": "nohash.zip", "sha256": "abc", "size": 1}]
    assert [p.name for p in addons.fetch_catalog(fetch=net)] == ["dune"]


def test_update_replaces_only_changed_installed_packs(sounds):
    net = FakeNet({"dune": DUNE, "star-wars": make_zip({"a.mp3": b"1"})})
    catalog = {p.name: p for p in addons.fetch_catalog(fetch=net)}
    addons.install(catalog["dune"], fetch=net)
    (sounds / "r2d2").mkdir(parents=True)  # bundled by the installer: no add-on state
    new_dune = make_zip({"spice.mp3": b"ID3 v2"})
    net.blobs["dune.zip"] = new_dune
    net.manifest["packs"][0] = entry("dune", new_dune)
    assert addons.update_installed(fetch=net) == ["dune"]
    assert (sounds / "dune" / "spice.mp3").read_bytes() == b"ID3 v2"
    assert not (sounds / "star-wars").exists()  # never installed: not auto-installed
    assert (sounds / "r2d2").is_dir()


def test_daily_check_runs_at_most_once_a_day(sounds):
    net = FakeNet({"dune": DUNE})
    assert addons.update_if_due(fetch=net, now=1_000_000.0) == []
    assert addons.update_if_due(fetch=net, now=1_000_000.0 + 3600) is None  # not due
    assert addons.update_if_due(fetch=net, now=1_000_000.0 + 86_401) == []
    assert net.calls.count(addons.MANIFEST_URL) == 2


def test_network_failure_is_a_structured_error(sounds):
    def offline(url, limit):
        raise OSError("no route")
    with pytest.raises(addons.AddonError) as err:
        addons.fetch_catalog(fetch=offline)
    assert err.value.kind == "network"


def test_remove_deletes_an_installed_pack(sounds):
    net = FakeNet({"dune": DUNE})
    addons.install(addons.fetch_catalog(fetch=net)[0], fetch=net)
    addons.remove("dune")
    assert not (sounds / "dune").exists() and addons.installed() == {}
