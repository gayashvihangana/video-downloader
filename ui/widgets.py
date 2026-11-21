"""Small view widgets. They only show things; the window decides what."""

from PIL import Image
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    FluentIcon,
    IconWidget,
    IndeterminateProgressRing,
    PrimaryPushButton,
    PushButton,
    SegmentedWidget,
    StrongBodyLabel,
    isDarkTheme,
)

from . import theme


def pil_to_pixmap(image: Image.Image) -> QPixmap:
    """PIL picture -> QPixmap (call on the UI thread)."""

    image = image.convert("RGBA")
    data = image.tobytes("raw", "RGBA")
    qimage = QImage(data, image.width, image.height, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(qimage.copy())  # copy: data is freed after this


class Choice(ComboBox):
    """A drop-down with simple get / set / values helpers."""

    def get(self):
        return self.currentText()

    def set(self, value):
        self.setCurrentText(value)

    def values(self):
        return [item.text for item in self.items]

    def set_values(self, values, current=None):
        if self.values() != list(values):
            self.blockSignals(True)
            self.clear()
            self.addItems(values)
            self.blockSignals(False)

        if current is not None:
            self.setCurrentText(current)


class ModeSelector(SegmentedWidget):
    """Video / Audio / Photos. Offers only what the link really has."""

    ICONS = {"Video": FluentIcon.VIDEO, "Audio": FluentIcon.MUSIC}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = []

    def values(self):
        return list(self._values)

    def set_values(self, values):
        if list(values) == self._values:
            return

        self.clear()
        self._values = list(values)

        for value in values:
            self.addItem(value, value, icon=self.ICONS.get(value, FluentIcon.PHOTO))

    def get(self):
        return self.currentRouteKey()

    def set(self, value):
        self.setCurrentItem(value)


class Thumbnail(QWidget):
    """Rounded preview picture, a placeholder icon, or a spinner."""

    RADIUS = 10

    def __init__(self, parent=None, width=192, height=108):
        super().__init__(parent)
        size = QSize(width, height)
        self.setFixedSize(size)
        self.pixmap = None
        self.icon = FluentIcon.VIDEO

        self.spinner = IndeterminateProgressRing(self, start=False)
        self.spinner.setFixedSize(32, 32)
        self.spinner.setStrokeWidth(3)
        self.spinner.move((size.width() - 32) // 2, (size.height() - 32) // 2)
        self.spinner.hide()

    def set_image(self, pixmap):
        self.pixmap = pixmap
        self.set_loading(False)
        self.update()

    def set_placeholder(self, icon):
        self.pixmap = None
        self.icon = icon
        self.set_loading(False)
        self.update()

    def set_loading(self, loading):
        if loading:
            self.pixmap = None
            self.icon = None
            self.spinner.show()
            self.spinner.start()
        else:
            self.spinner.stop()
            self.spinner.hide()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(
            QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
        )

        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, self.RADIUS, self.RADIUS)
        painter.setClipPath(path)

        dark = isDarkTheme()
        painter.fillRect(
            rect, QColor(255, 255, 255, 13) if dark else QColor(0, 0, 0, 10)
        )

        if self.pixmap and not self.pixmap.isNull():
            # Fit inside (portrait TikToks stay whole), centred on the backdrop
            ratio = self.devicePixelRatioF()
            scaled = self.pixmap.scaled(
                self.size() * ratio,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            scaled.setDevicePixelRatio(ratio)
            width, height = scaled.width() / ratio, scaled.height() / ratio
            x = (self.width() - width) / 2
            y = (self.height() - height) / 2
            painter.drawPixmap(int(x), int(y), scaled)

        elif self.icon is not None:
            side = 28
            icon_rect = QRectF(
                (self.width() - side) / 2, (self.height() - side) / 2, side, side
            )
            painter.setOpacity(0.45)
            self.icon.render(painter, icon_rect)


class TintedFrame(QFrame):
    """A frame that paints its own rounded background, per theme.

    Subclasses set LIGHT / DARK fill (and optional border) colours.
    """

    LIGHT, DARK = QColor(0, 0, 0, 9), QColor(255, 255, 255, 10)
    LIGHT_BORDER = DARK_BORDER = None
    RADIUS = 8

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        dark = isDarkTheme()
        border = self.DARK_BORDER if dark else self.LIGHT_BORDER
        painter.setPen(border if border is not None else Qt.PenStyle.NoPen)
        painter.setBrush(self.DARK if dark else self.LIGHT)
        painter.drawRoundedRect(
            QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), self.RADIUS, self.RADIUS
        )


class Tag(TintedFrame):
    """A small rounded label with an icon, e.g. "Video", "4 photos"."""

    LIGHT, DARK = QColor(0, 0, 0, 13), QColor(255, 255, 255, 18)
    RADIUS = 10

    def __init__(self, icon, text, parent=None):
        super().__init__(parent)
        self.text = text

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 3, 10, 3)
        layout.setSpacing(5)

        icon_widget = IconWidget(icon, self)
        icon_widget.setFixedSize(12, 12)
        layout.addWidget(icon_widget)
        layout.addWidget(CaptionLabel(text, self))


class TagRow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(6)
        self.layout_.addStretch(1)
        self.tags = []

    def set_tags(self, tags):
        """tags: [(FluentIcon, text)]"""

        for tag in self.tags:
            self.layout_.removeWidget(tag)
            tag.deleteLater()

        self.tags = [Tag(icon, text, self) for icon, text in tags]

        for index, tag in enumerate(self.tags):
            self.layout_.insertWidget(index, tag)

        self.setVisible(bool(self.tags))

    def text(self):
        return "  ".join(tag.text for tag in self.tags)


class Banner(TintedFrame):
    """A tinted strip with a message and buttons (used for updates)."""

    LIGHT, DARK = QColor("#fdeced"), QColor("#3a1d20")
    LIGHT_BORDER, DARK_BORDER = QColor("#f6c4c6"), QColor("#5a2a2e")

    def __init__(self, icon, parent=None):
        super().__init__(parent)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 10, 10)
        layout.setSpacing(12)

        icon_widget = IconWidget(icon, self)
        icon_widget.setFixedSize(18, 18)
        layout.addWidget(icon_widget)

        self.label = BodyLabel(self)
        self.label.setWordWrap(True)
        layout.addWidget(self.label, 1)

        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(8)
        layout.addLayout(self.buttons)

    def add_button(self, button):
        self.buttons.addWidget(button)
        return button


class SignInPanel(TintedFrame):
    """ "Sign in to view this": shown in the preview when a link needs it."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        icon = IconWidget(FluentIcon.PEOPLE, self)
        icon.setFixedSize(16, 16)
        head.addWidget(icon)
        head.addWidget(StrongBodyLabel("Sign in to view this", self))
        head.addStretch(1)
        layout.addLayout(head)

        self.text = BodyLabel(self)
        self.text.setWordWrap(True)
        theme.paint(self.text, theme.MUTED)
        layout.addWidget(self.text)

        self.button_row = QHBoxLayout()
        self.button_row.setSpacing(8)
        self.button_row.addStretch(1)
        layout.addLayout(self.button_row)
        self.buttons = []

        self.status = CaptionLabel(self)
        self.status.setWordWrap(True)
        self.status.hide()
        self.level = theme.MUTED
        layout.addWidget(self.status)

    def set_buttons(self, buttons):
        """buttons: [(text, callback, primary)]"""

        for button in self.buttons:
            self.button_row.removeWidget(button)
            button.deleteLater()

        self.buttons = []

        for index, (text, callback, primary) in enumerate(buttons):
            button = (PrimaryPushButton if primary else PushButton)(text, self)
            button.clicked.connect(callback)
            self.button_row.insertWidget(index, button)
            self.buttons.append(button)

    def button_texts(self):
        return [button.text() for button in self.buttons]

    def set_buttons_enabled(self, enabled):
        for button in self.buttons:
            button.setEnabled(enabled)

    def set_status(self, text, color=theme.MUTED):
        """Status line under the buttons; takes no space while empty."""

        self.level = color
        self.status.setText(text)
        theme.paint(self.status, color)
        self.status.setVisible(bool(text))
