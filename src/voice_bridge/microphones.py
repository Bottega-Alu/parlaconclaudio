"""Microphones as Windows shows them: one entry per active input, saved by name.

PortAudio lists every input once per Windows audio system (MME, DirectSound,
WASAPI, WDM-KS), adds kernel filters of unplugged or disabled devices and
Bluetooth hands-free profiles, and renumbers everything whenever a device is
plugged in or out. Decision tree for the list the user picks from:

1. WASAPI lists only the endpoints Windows has active, with full names: those
   are the microphones (the ones in Windows Settings > Sound).
2. Capture runs through MME, which converts to the 16 kHz Whisper needs. MME
   truncates names to 31 characters, so each microphone is paired with the
   unused MME input whose name is the start of the full name.
3. No WASAPI (unusual): the MME inputs, minus the first one, which PortAudio
   always reports as the "Sound Mapper" alias of the Windows default.

The choice is saved by name (indexes are not stable); no name = Windows default.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Microphone:
    name: str        # full name, as in Windows Settings
    index: int       # MME device index to open for capture (valid until devices change)
    is_default: bool  # the current Windows default input


def _host_api(pa, name):
    for index in range(pa.get_host_api_count()):
        info = pa.get_host_api_info_by_index(index)
        if info.get("name") == name:
            return info
    return None


def _inputs(pa, api_index):
    for index in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(index)
        if info.get("hostApi") == api_index and info.get("maxInputChannels", 0) > 0:
            yield index, info.get("name", "")


def list_microphones(pa):
    """Active Windows microphones, each once, with the MME index to record from."""
    mme = _host_api(pa, "MME")
    if mme is None:
        return []
    mme_inputs = list(_inputs(pa, mme["index"]))
    default = mme.get("defaultInputDevice", -1)
    wasapi = _host_api(pa, "Windows WASAPI")
    if wasapi is None:
        return [Microphone(name, index, index == default) for index, name in mme_inputs[1:]]
    microphones, used = [], set()
    for _index, full_name in _inputs(pa, wasapi["index"]):
        match = next((index for index, name in mme_inputs
                      if index not in used and name and full_name.startswith(name)), None)
        if match is None or any(mic.name == full_name for mic in microphones):
            continue
        used.add(match)
        microphones.append(Microphone(full_name, match, match == default))
    return microphones


def find(pa, name):
    """The connected microphone called *name*, or None (unplugged, renamed, or no name)."""
    if not name:
        return None
    return next((mic for mic in list_microphones(pa) if mic.name == name), None)


def choices(devices, config):
    """What the user picks from: ({label: saved value}, default name, unplugged saved name).

    *devices* come from AudioRecorder.list_devices(). The first entry is the Windows
    default (value None, label left untranslated for the caller). A saved microphone that
    is not connected stays listed, so the choice is visible and can be changed.
    """
    from .i18n import N_
    mics = {N_("Predefinito di Windows"): None}
    mics.update({device.name: device.name for device in devices})
    default = next((device.name for device in devices if getattr(device, "is_default", False)), None)
    saved = config.get("mic_device_name")
    missing = saved if saved and saved not in mics.values() else None
    if missing:
        mics[missing] = missing
    return mics, default, missing
