from test_controller import controller
from test_settings_dialog import Doctor

from codex_stt_tray.models import State
from codex_stt_tray.settings import Settings
from codex_stt_tray.settings_dialog import SettingsDialog
from codex_stt_tray.tray import Tray, state_icon


def test_tray_states_and_icons(qtbot):
    c = controller()
    tray = Tray(c, c.settings)
    for state in State:
        c.state = state
        c.changed.emit(state)
        assert not tray.icon().isNull()
        assert tray.primary.isEnabled() == (state not in (State.TRANSCRIBING, State.COPYING))
    assert (
        state_icon(State.IDLE).pixmap(32).toImage()
        != state_icon(State.RECORDING).pixmap(32).toImage()
    )
    tray.timer.stop()
    tray.menu.deleteLater()
    tray.deleteLater()


def test_settings_and_exit_remain_available_after_error(qtbot):
    c = controller()
    tray = Tray(c, c.settings)
    requested = []
    tray.settings_requested.connect(lambda: requested.append("settings"))
    tray.quit_requested.connect(lambda: requested.append("exit"))
    c.state = State.ERROR
    c.changed.emit(c.state)
    assert tray.contextMenu() is tray.menu
    actions = {action.text(): action for action in tray.menu.actions()}
    for label in ("Settings…", "Exit"):
        assert actions[label].isEnabled()
        actions[label].trigger()
    assert requested == ["settings", "exit"]
    tray.menu.close()
    c.cancel()


def test_quick_preferences_persist_and_sync_without_losing_drafts(qtbot, monkeypatch):
    from codex_stt_tray import tray as tray_module

    monkeypatch.setattr(tray_module.QMediaDevices, "audioInputs", lambda: [])
    c = controller()
    settings = c.settings = Settings()
    tray = Tray(c, settings)
    dialog = SettingsDialog(settings, Doctor(settings), c)
    qtbot.addWidget(dialog)
    tray.preference_changed.connect(dialog.refresh_preference)
    dialog.home.setText("unsaved-home-draft")
    tray._populate_preferences()
    duration = tray.quick_menus["duration_limit"]
    next(a for a in duration.actions() if a.text() == "10 minutes").trigger()
    assert Settings().duration_limit == 600
    assert dialog.duration.value() == 600
    assert dialog.home.text() == "unsaved-home-draft"
    chime = tray.quick_menus["volume"]
    next(a for a in chime.actions() if a.text() == "Muted").trigger()
    assert Settings().volume == 0.0 and dialog.volume.currentData() == 0.0

    dialog.duration.setValue(3000)
    dialog.save()
    assert Settings().duration_limit == 3000
    tray._populate_preferences()
    checked = [a.text() for a in duration.actions() if a.isChecked()]
    assert checked == ["Custom: 3000 seconds"]
    c.state = State.RECORDING
    c.changed.emit(c.state)
    assert not duration.isEnabled()
    tray._save_preference("duration_limit", 180)
    assert Settings().duration_limit == 3000
    c.cancel()
    tray.menu.deleteLater()
    tray.deleteLater()
