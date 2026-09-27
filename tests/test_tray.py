from test_controller import controller

from codex_stt_tray.models import State
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
