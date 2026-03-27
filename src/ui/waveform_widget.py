import numpy as np
from PyQt6.QtCore import QTimer, QRectF
from PyQt6.QtGui import QPainter, QColor, QPainterPath
from PyQt6.QtWidgets import QWidget

from ui.theme import Theme


class WaveformWidget(QWidget):
    BAR_COUNT = 40
    BAR_GAP = 2.5
    BAR_RADIUS = 1.5
    BAR_MIN_H = 2.0
    FPS = 30
    LERP = 0.25

    def __init__(self, parent=None, compact: bool = False):
        super().__init__(parent)
        self._compact = compact
        if compact:
            self.BAR_COUNT = 24
            self.BAR_GAP = 2.0
        self._levels = np.zeros(self.BAR_COUNT)
        self._target = np.zeros(self.BAR_COUNT)
        self._color = Theme.WAVEFORM_ACTIVE
        self._frozen = False

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000 // self.FPS)

    def update_data(self, pcm_chunk: bytes):
        if self._frozen:
            return
        samples = np.frombuffer(pcm_chunk, dtype=np.int16).astype(np.float32) / 32768.0
        n = len(samples)
        if n == 0:
            return
        chunk_size = max(1, n // self.BAR_COUNT)
        target = np.zeros(self.BAR_COUNT)
        for i in range(self.BAR_COUNT):
            start = i * chunk_size
            end = min(start + chunk_size, n)
            if start < n:
                seg = samples[start:end]
                rms = np.sqrt(np.mean(seg ** 2))
                peak = np.max(np.abs(seg))
                target[i] = rms * 0.6 + peak * 0.4
        scale = 3.5
        self._target = np.clip(target * scale, 0.0, 1.0)

    def freeze(self):
        self._frozen = True
        self._color = Theme.WAVEFORM_FROZEN

    def unfreeze(self):
        self._frozen = False
        self._color = Theme.WAVEFORM_ACTIVE
        self._target = np.zeros(self.BAR_COUNT)

    def reset(self):
        self._levels = np.zeros(self.BAR_COUNT)
        self._target = np.zeros(self.BAR_COUNT)
        self._frozen = False
        self._color = Theme.WAVEFORM_ACTIVE
        self.update()

    def _tick(self):
        self._levels += (self._target - self._levels) * self.LERP
        if not self._frozen:
            self._target *= 0.92
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        h = self.height()
        total_gap = (self.BAR_COUNT - 1) * self.BAR_GAP
        bar_w = max(2.0, (w - total_gap) / self.BAR_COUNT)
        cy = h / 2.0

        color = QColor(self._color)

        for i in range(self.BAR_COUNT):
            x = i * (bar_w + self.BAR_GAP)
            level = self._levels[i]
            bar_h = max(self.BAR_MIN_H, level * h * 0.85)
            half_h = bar_h / 2.0
            rect = QRectF(x, cy - half_h, bar_w, bar_h)
            path = QPainterPath()
            path.addRoundedRect(rect, self.BAR_RADIUS, self.BAR_RADIUS)
            p.fillPath(path, color)

        p.end()
