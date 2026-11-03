"""Microphone list: one entry per active Windows input, matched to MME, saved by name."""
from types import SimpleNamespace

from src.voice_bridge import microphones

MME, DSOUND, WASAPI, WDMKS = 0, 1, 2, 3
APIS = {MME: "MME", DSOUND: "Windows DirectSound", WASAPI: "Windows WASAPI", WDMKS: "Windows WDM-KS"}


class FakePortAudio:
    def __init__(self, devices, default_input, apis=APIS):
        self.devices, self.default_input, self.apis = devices, default_input, apis

    def get_host_api_count(self):
        return len(self.apis)

    def get_host_api_info_by_index(self, index):
        api = list(self.apis)[index]
        return {"index": api, "name": self.apis[api],
                "defaultInputDevice": self.default_input if api == MME else -1}

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        api, name, channels = self.devices[index]
        return {"hostApi": api, "name": name, "maxInputChannels": channels}


BCC = "Echo Cancelling Speakerphone (BCC950 ConferenceCam)"
# What PortAudio reported on a real PC with one active microphone (2026-11-03).
REAL_PC = [
    (MME, "Microsoft Sound Mapper - Input", 2), (MME, BCC[:31], 2), (MME, "Speakers", 0),
    (DSOUND, "Primary Sound Capture Driver", 2), (DSOUND, BCC, 2),
    (WASAPI, BCC, 2), (WASAPI, "Speakers", 0),
    (WDMKS, BCC, 2), (WDMKS, "Microphone (Realtek USB Audio)", 2), (WDMKS, "Microphone (Realtek USB Audio)", 2),
    (WDMKS, "Input ()", 2), (WDMKS, "Headset (Hands-Free (JBL TOUR PRO2))", 1),
]


def test_one_entry_per_active_microphone_with_full_name_and_mme_index():
    mics = microphones.list_microphones(FakePortAudio(REAL_PC, default_input=1))
    assert mics == [microphones.Microphone(BCC, 1, True)]


def test_truncated_mme_names_pair_with_the_right_microphone():
    first, second = "Microphone (USB PnP Sound Device)", "Microphone (2- USB PnP Sound Device)"
    devices = [(MME, "Microsoft Sound Mapper - Input", 2), (MME, second[:31], 1), (MME, first[:31], 1),
               (WASAPI, first, 1), (WASAPI, second, 1)]
    mics = microphones.list_microphones(FakePortAudio(devices, default_input=2))
    assert [(mic.name, mic.index, mic.is_default) for mic in mics] == [(first, 2, True), (second, 1, False)]


def test_without_wasapi_the_mme_inputs_minus_the_sound_mapper():
    devices = [(MME, "Microsoft Sound Mapper - Input", 2), (MME, "Microfono", 1)]
    pa = FakePortAudio(devices, default_input=1, apis={MME: "MME"})
    assert microphones.list_microphones(pa) == [microphones.Microphone("Microfono", 1, True)]


def test_find_by_name_survives_renumbering():
    pa = FakePortAudio(REAL_PC, default_input=1)
    assert microphones.find(pa, BCC).index == 1
    renumbered = [(MME, "Microsoft Sound Mapper - Input", 2), (MME, "Microphone (USB)", 1), (MME, BCC[:31], 2),
                  (WASAPI, "Microphone (USB)", 1), (WASAPI, BCC, 2)]
    assert microphones.find(FakePortAudio(renumbered, default_input=1), BCC).index == 2
    assert microphones.find(pa, "Unplugged headset") is None
    assert microphones.find(pa, None) is None


def test_choices_keep_an_unplugged_saved_microphone_visible():
    devices = [SimpleNamespace(name=BCC, device_id=1, is_default=True)]
    mics, default, missing = microphones.choices(devices, {"mic_device_name": "Cuffie Jabra"})
    assert list(mics.values()) == [None, BCC, "Cuffie Jabra"]
    assert default == BCC and missing == "Cuffie Jabra"
    assert microphones.choices(devices, {"mic_device_name": BCC})[2] is None
