from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QSoundEffect


class Feedback(QObject):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.sound = QSoundEffect(self)
        self.sound.setSource(
            QUrl.fromLocalFile(str(Path(__file__).parent / "resources/complete.wav"))
        )
        self.sound.setLoopCount(1)

    def play(self):
        self.sound.setVolume(self.settings.volume)
        if self.settings.volume > 0:
            self.sound.play()
