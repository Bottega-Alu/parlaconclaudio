from types import SimpleNamespace
from src.voice_bridge import tray_icon


def test_idle_animation_has_no_periodic_render(monkeypatch):
    animator = tray_icon._IconAnimator()
    rendered = []
    monkeypatch.setattr(tray_icon, '_render_icon_frame', lambda *a, **kw: rendered.append(1) or object())
    animator._icon_ref = SimpleNamespace(icon=None)
    animator._running = True
    waits = []
    def wait(timeout):
        waits.append(timeout)
        animator._running = False
    animator._wake = SimpleNamespace(clear=lambda: None, wait=wait)
    animator._animation_loop()
    assert waits == [None]
    assert len(rendered) == 1


def test_active_animation_cache_is_bounded(monkeypatch):
    animator = tray_icon._IconAnimator()
    calls = []
    monkeypatch.setattr(tray_icon, '_render_icon_frame', lambda *a, **kw: calls.append(1) or object())
    frames = [animator._frame('recording', idx) for idx in range(48)]
    assert len(calls) == 12
    assert frames[0] is frames[12]
    assert animator._frame('idle', 0) is animator._frame('idle', 50)


def test_mic_rejection_does_not_persist(monkeypatch):
    tray = tray_icon.TrayIcon(on_mic_changed=lambda device: False)
    monkeypatch.setattr(tray_icon, '_save_config', lambda config: (_ for _ in ()).throw(AssertionError('must not save')))
    monkeypatch.setattr(tray, '_notify', lambda text: None)
    tray._make_set_mic(12)(None, None)


def test_file_dialog_busy_guard(monkeypatch):
    tray = tray_icon.TrayIcon(transcriber=object())
    tray._batch_lock.acquire()
    messages = []
    monkeypatch.setattr(tray, '_notify', messages.append)
    tray._transcribe_files_clicked(None, None)
    assert messages
    tray._batch_lock.release()


def test_not_loaded_engine_does_not_claim_loading():
    tray = tray_icon.TrayIcon()
    status = tray._get_stt_status({})
    assert status['active_label'] == 'In attesa del primo utilizzo'
    assert status['gpu_state'] == 'not_loaded'
