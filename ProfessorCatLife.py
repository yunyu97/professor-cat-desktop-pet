"""
Professor Cat (Life edition) - a desktop Pomodoro cat that moves more like a real cat.

What makes the motion more natural than ProfessorCatReal.py:
  * Normalized sprites (see prepare_life_sprites.py): every frame shares one pixel
    scale and one ground anchor, so the cat no longer grows/shrinks/jitters per frame.
  * Physics-based walking: the cat accelerates, cruises at a per-trip speed, and
    eases to a stop. The walk cycle is driven by distance travelled, so paws don't
    slide on the floor.
  * Turns are deliberate: it stops, hesitates, then turns around.
  * A behaviour "brain" with weighted choices: wander, pause and look around, sit,
    groom, nap, investigate the mouse cursor.
  * Breathing while resting, a soft contact shadow, a pick-up dangle that swings
    with your drag, and gravity + a squash on landing when you drop it.
"""

import json
import math
import random
import sys
from pathlib import Path

from PySide6.QtCore import (QCoreApplication, QElapsedTimer, QObject, QPointF, QRectF,
                            QSettings, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QBrush, QColor, QCursor, QFont, QFontMetrics, QImage,
                           QLinearGradient, QPainter, QPainterPath, QPen, QPixmap, QPolygonF,
                           QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QInputDialog, QMenu, QMessageBox,
                               QWidget)

MUSIC_URL = "https://www.youtube.com/watch?v=2z0-jwR4dQM&list=PLC4hV48v--Io"

# Which program this is. ProfessorCatStudy.py switches to the "study" edition,
# which ships with no music and lets the user add their own study playlist.
EDITION = {
    "settings": "Life",          # separate saved settings per edition
    "default_url": MUSIC_URL,    # "" = no built-in music
    "study": False,
    "title": "Professor Cat",
}
IS_MAC = sys.platform == "darwin"


# ----------------------------------------------------------------------
# Background music
# ----------------------------------------------------------------------
PLAYER_HTML = """<!doctype html><html><head><meta charset="utf-8">
<style>html,body{margin:0;height:100%;background:#000;overflow:hidden}</style></head>
<body><div id="p"></div>
<script src="https://www.youtube.com/iframe_api"></script>
<script>
var player = null, ready = false, err = null;
function onYouTubeIframeAPIReady() {
  var pv = {autoplay: 1, playsinline: 1, rel: 0, loop: 1};
  if (LIST_ID) { pv.list = LIST_ID; pv.listType = 'playlist'; }
  else { pv.playlist = VIDEO_ID; }  /* lets a single video loop */
  player = new YT.Player('p', {
    width: '100%', height: '100%', videoId: VIDEO_ID, playerVars: pv,
    events: {
      onReady: function (e) { ready = true; e.target.setVolume(START_VOL);
                              if (START_PLAYING) e.target.playVideo(); },
      onError: function (e) { err = e.data; if (player && player.nextVideo) player.nextVideo(); }
    }
  });
}
function catStatus() {
  if (!ready) return JSON.stringify({ready: false, err: err});
  var d = player.getVideoData() || {};
  return JSON.stringify({ready: true, state: player.getPlayerState(), title: d.title || '',
                         vol: player.getVolume(), muted: player.isMuted(), err: err,
                         idx: player.getPlaylistIndex(), n: (player.getPlaylist() || []).length});
}
</script></body></html>"""


def parse_youtube_link(text):
    """Return (video_id, list_id) from any common YouTube link (or None)."""
    url = QUrl(text.strip())
    host = url.host().lower()
    from urllib.parse import parse_qs
    q = parse_qs(url.query())
    vid = (q.get("v") or [""])[0]
    lst = (q.get("list") or [""])[0]
    if "youtu.be" in host:
        vid = url.path().strip("/").split("/")[0]
    elif "/embed/" in url.path() or "/shorts/" in url.path() or "/live/" in url.path():
        vid = url.path().rstrip("/").split("/")[-1]
    if not (vid or lst) or "youtu" not in host:
        return None
    return vid, lst


class YouTubeMusic(QObject):
    """Hidden YouTube player (official IFrame API) that loads lazily, so the
    browser engine never delays the cat from appearing."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings = QSettings("ProfessorCat", EDITION["settings"])
        self.url = self.settings.value("music/url", EDITION["default_url"]) or ""
        self.volume = int(self.settings.value("music/volume", 60))
        self.enabled = self.settings.value("music/enabled", "true") == "true"
        self.view = None
        self.ready = False
        self.playing = False
        self.muted = False
        self.title = ""
        self.track = (0, 0)
        self.error = None
        self.want_playing = True
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.refresh)

    # -- lifecycle -----------------------------------------------------
    @property
    def has_source(self):
        return parse_youtube_link(self.url) is not None

    def start(self):
        if self.view is not None or not self.enabled or not self.has_source:
            return
        try:
            from PySide6.QtWebEngineCore import QWebEngineSettings
            from PySide6.QtWebEngineWidgets import QWebEngineView
        except ImportError:
            self.error = "Qt WebEngine is not installed"
            self.changed.emit()
            return

        class PlayerWindow(QWebEngineView):
            def closeEvent(self, e):  # closing the video window just hides it
                e.ignore()
                self.hide()

        self.view = PlayerWindow()
        self.view.setWindowTitle(f"{EDITION['title']} - music")
        self.view.resize(400, 225)
        self.view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        self.load()
        self.poll.start(1000)

    def load(self):
        parsed = parse_youtube_link(self.url) or parse_youtube_link(EDITION["default_url"])
        if parsed is None:
            return
        vid, lst = parsed
        html = (PLAYER_HTML.replace("LIST_ID", json.dumps(lst))
                .replace("VIDEO_ID", json.dumps(vid))
                .replace("START_VOL", str(self.volume))
                .replace("START_PLAYING", "true" if self.want_playing else "false"))
        self.ready = False
        self.title = ""
        # A real https origin is needed: YouTube refuses to play embeds without one.
        self.view.setHtml(html, QUrl("https://localhost/"))

    def shutdown(self):
        self.poll.stop()
        if self.view is not None:
            self.view.deleteLater()
            self.view = None
        self.ready = self.playing = False
        self.title = ""

    # -- state ---------------------------------------------------------
    def refresh(self):
        if self.view is None:
            return
        self.view.page().runJavaScript(
            "typeof catStatus === 'function' ? catStatus() : ''", 0, self._on_status)

    def _on_status(self, result):
        if not result:
            return
        try:
            s = json.loads(result)
        except (TypeError, ValueError):
            return
        self.ready = s.get("ready", False)
        self.error = s.get("err")
        if self.ready:
            self.playing = s.get("state") in (1, 3)   # playing or buffering
            self.title = s.get("title") or ""
            self.muted = bool(s.get("muted"))
            self.volume = int(s.get("vol", self.volume))
            self.track = (int(s.get("idx") or 0) + 1, int(s.get("n") or 0))
        self.changed.emit()

    def js(self, code):
        if self.view is not None:
            self.view.page().runJavaScript(f"if (ready) {{ {code} }}")
            QTimer.singleShot(250, self.refresh)

    # -- controls ------------------------------------------------------
    def play(self):
        self.want_playing = True
        if self.view is None:
            self.start()
        self.js("player.playVideo();")

    def pause(self):
        self.want_playing = False
        self.js("player.pauseVideo();")

    def toggle(self):
        self.pause() if self.playing else self.play()

    def next(self):
        self.js("player.nextVideo();")

    def previous(self):
        # like most players: restart the song unless we are at its very start
        self.js("if (player.getCurrentTime() > 4) player.seekTo(0, true); else player.previousVideo();")

    def change_volume(self, delta):
        self.volume = max(0, min(100, self.volume + delta))
        self.settings.setValue("music/volume", self.volume)
        self.js(f"player.unMute(); player.setVolume({self.volume});")

    def toggle_mute(self):
        self.js("player.isMuted() ? player.unMute() : player.mute();")

    def set_url(self, url):
        self.url = url or ""
        self.settings.setValue("music/url", self.url)
        self.want_playing = True
        if not self.has_source:      # playlist removed
            self.shutdown()
        elif self.view is None:
            self.start()
        else:
            self.load()
        self.changed.emit()

    def set_enabled(self, on):
        self.enabled = on
        self.settings.setValue("music/enabled", "true" if on else "false")
        if on:
            self.start()
        else:
            self.shutdown()
            self.playing = False
            self.ready = False
        self.changed.emit()

    def toggle_video(self):
        if self.view is None:
            self.start()
        if self.view is not None:
            self.view.setVisible(not self.view.isVisible())
            if self.view.isVisible():
                self.view.raise_()


def resource_path(relative_path):
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parent
    return base / relative_path


def approach(value, target, max_delta):
    if value < target:
        return min(value + max_delta, target)
    return max(value - max_delta, target)


# ----------------------------------------------------------------------
# Rest reminder: cat bed + speech bubble
# ----------------------------------------------------------------------
BED_W = 204.0  # fits the curled-up sleeping cat


def draw_cat_bed(p, cx, ground, part="back", glow=0.0):
    """Round, plush donut bed seen slightly from above.
    'back'  = glow, shadow, side wall, rim and cushion (drawn behind the cat);
    'front' = the near half of the wall + rim, drawn over the cat so it looks tucked in."""
    rx, ry = BED_W / 2, 24.0
    top_c = ground - 24          # centre of the rim's top surface
    bot_c = ground - 6           # centre of the bed's base
    body = QPainterPath()        # silhouette: top ellipse + side wall + base ellipse
    body.addEllipse(QPointF(cx, top_c), rx, ry)
    body.addRect(QRectF(cx - rx, top_c, 2 * rx, bot_c - top_c))
    body.addEllipse(QPointF(cx, bot_c), rx, ry)
    body.setFillRule(Qt.FillRule.WindingFill)
    body = body.simplified()
    top = QPainterPath()
    top.addEllipse(QPointF(cx, top_c), rx, ry)
    hole = QPainterPath()
    hole.addEllipse(QPointF(cx, top_c - 1), rx - 28, ry - 10)

    wall_grad = QLinearGradient(QPointF(0, top_c), QPointF(0, bot_c + ry))
    wall_grad.setColorAt(0.0, QColor("#8f553b"))
    wall_grad.setColorAt(1.0, QColor("#5f3524"))
    rim_grad = QRadialGradient(QPointF(cx, top_c - ry * 0.6), rx * 1.1)
    rim_grad.setColorAt(0.0, QColor("#e2a57d"))
    rim_grad.setColorAt(0.7, QColor("#bf7d58"))
    rim_grad.setColorAt(1.0, QColor("#9d5f42"))
    outline = QPen(QColor("#5a3322"), 1.2)
    seam_pen = QPen(QColor(255, 236, 214, 120), 1.1, Qt.PenStyle.DashLine)
    seam_rect = QRectF(cx - rx + 13, top_c - ry + 6, 2 * rx - 26, 2 * ry - 12)

    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    if part == "back":
        if glow > 0:
            g = QRadialGradient(QPointF(cx, top_c + 4), rx + 30)
            g.setColorAt(0.55, QColor(255, 214, 120, int(130 * glow)))
            g.setColorAt(1.0, QColor(255, 214, 120, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(cx, top_c + 4), rx + 30, ry + 34)
        # floor shadow
        sh = QRadialGradient(QPointF(cx, ground + 4), rx + 10)
        sh.setColorAt(0.0, QColor(0, 0, 0, 80))
        sh.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.save()
        p.translate(cx, ground + 4)
        p.scale(1.0, 0.2)
        p.translate(-cx, -(ground + 4))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(sh))
        p.drawEllipse(QPointF(cx, ground + 4), rx + 10, rx + 10)
        p.restore()
        # side wall, then the puffy rim on top
        p.setPen(outline)
        p.setBrush(QBrush(wall_grad))
        p.drawPath(body)
        p.setBrush(QBrush(rim_grad))
        p.drawPath(top)
        # the hole: inner wall in shadow, cushion sitting a little lower
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#7a452f"))
        p.drawPath(hole)
        p.save()
        p.setClipPath(hole)
        cg = QRadialGradient(QPointF(cx, top_c + 6), rx - 30)
        cg.setColorAt(0.0, QColor("#fbf1e4"))
        cg.setColorAt(0.75, QColor("#ead8c1"))
        cg.setColorAt(1.0, QColor("#cbb093"))
        p.setBrush(QBrush(cg))
        p.drawEllipse(QPointF(cx, top_c + 5), rx - 30, ry - 11)
        p.restore()
        p.setPen(seam_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(seam_rect, 0, 180 * 16)
    else:
        near = QPainterPath()
        near.addRect(QRectF(cx - rx - 4, top_c - 1, 2 * rx + 8, ry * 3))
        front = body.subtracted(hole).intersected(near)
        p.setClipPath(front)
        p.setPen(outline)
        p.setBrush(QBrush(wall_grad))
        p.drawPath(body)
        p.setBrush(QBrush(rim_grad))
        p.drawPath(top)
        p.setPen(Qt.PenStyle.NoPen)
        p.setClipping(False)
        p.setPen(seam_pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(seam_rect, 180 * 16, 180 * 16)
    p.restore()


class CatBed(QWidget):
    """The bed is its own little window sitting on the cat's floor line."""
    W, H, GROUND = 260, 110, 80

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool
                            | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(self.W, self.H)
        self.glow = 0.0
        self.invite = False   # gentle pulse while it is waiting for the cat
        self.t = 0.0

    def place(self, center_x, ground_y):
        self.move(int(center_x - self.W / 2), int(ground_y - self.GROUND))

    def center(self):
        return self.x() + self.W / 2, self.y() + self.GROUND

    def advance(self, dt, hovered):
        self.t += dt
        target = 1.0 if hovered else (0.35 + 0.25 * math.sin(self.t * 3.0) if self.invite else 0.0)
        self.glow = approach(self.glow, target, dt * 4)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        draw_cat_bed(p, self.W / 2, self.GROUND, "back", self.glow)
        if self.invite:
            # little hint so it's obvious what to do
            p.setPen(QColor(90, 60, 40, int(150 + 80 * self.glow)))
            p.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            p.drawText(QRectF(0, self.H - 20, self.W, 18), Qt.AlignmentFlag.AlignCenter,
                       "drop the cat here")
        p.end()


class SpeechBubble(QWidget):
    """Speech bubble that floats above the cat. Click it to dismiss."""
    W, H = 250, 104
    dismissed = Signal()

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.resize(self.W, self.H)
        self.text = ""
        self.progress = 1.0     # remaining time, 1 -> 0
        self.tail_x = self.W / 2
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to dismiss")

    def mousePressEvent(self, e):
        self.dismissed.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        body = QRectF(4, 4, self.W - 8, self.H - 22)
        path = QPainterPath()
        path.addRoundedRect(body, 14, 14)
        tail = QPainterPath()
        tx = max(body.left() + 24, min(body.right() - 24, self.tail_x))
        tail.moveTo(tx - 11, body.bottom() - 2)
        tail.lineTo(tx + 3, self.H - 3)
        tail.lineTo(tx + 12, body.bottom() - 2)
        tail.closeSubpath()
        path = path.united(tail)
        p.setPen(QPen(QColor("#6b4a36"), 1.6))
        p.setBrush(QColor("#fffaf2"))
        p.drawPath(path)
        p.setPen(QColor("#40352f"))
        f = QFont("Segoe UI", 9)
        p.setFont(f)
        p.drawText(body.adjusted(12, 8, -12, -14),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
                   | Qt.TextFlag.TextWordWrap, self.text)
        # time-left bar
        bar = QRectF(body.left() + 16, body.bottom() - 10, body.width() - 32, 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(107, 74, 54, 40))
        p.drawRoundedRect(bar, 2, 2)
        p.setBrush(QColor("#c98f6b"))
        p.drawRoundedRect(QRectF(bar.x(), bar.y(), bar.width() * self.progress, bar.height()), 2, 2)
        p.end()


class LifelikeCat(QWidget):
    W, H = 240, 294          # window size
    GROUND_Y = 182           # y (inside the window) where the paws touch the floor
    # one global scale for every frame -> no size popping (0.70 keeps the on-screen
    # size the idle cat always had; the idle frames are now stored 15% larger)
    SPRITE_SCALE = 0.70

    # Movement tuning (pixels / second)
    WALK_SPEED = 46.0
    RUN_SPEED = 150.0        # zoomies: a real gallop, not fast walking
    RUN_ACCEL = 240.0
    RUN_DECEL = 200.0
    ACCEL = 70.0
    DECEL = 90.0
    STRIDE_PX = 62.0         # distance covered by one full walk cycle at SPRITE_SCALE=1
    GRAVITY = 2200.0

    PRESETS = {"25 / 5 minutes": (25, 5), "50 / 10 minutes": (50, 10)}
    MANUAL_BREAK_MINUTES = 15

    # Rest reminder
    REST_AFTER_SECONDS = 2 * 60 * 60   # accumulated work time before the cat insists
    REMINDER_SECONDS = 30              # how long the bubble + bed wait for you
    SNOOZE_SECONDS = 15 * 60           # if ignored, ask again after this much more work
    BED_SINK = 12                      # how far above the floor the cushion holds the cat

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint
                            | Qt.WindowType.WindowStaysOnTopHint
                            | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
        self.resize(self.W, self.H)
        self.setMouseTracking(True)
        self.hover_btn = None
        self.pressed_btn = False

        # ---- sprites ---------------------------------------------------
        self.sprite_dir = resource_path("cat_sprites_life")
        self.frames = {name: self.load_frames(name)
                       for name in ("walk", "stand", "sit", "groom", "sleep", "lie")}
        if not self.frames["walk"] or not self.frames["stand"]:
            QMessageBox.critical(None, "Professor Cat",
                                 f"Sprites not found in:\n{self.sprite_dir}\n\n"
                                 "Run prepare_life_sprites.py first.")
            raise RuntimeError("Sprite assets not found.")
        self.flipped_cache = {}
        self.rig = self.load_rig()
        self.grain = self.make_grain()
        # Fall back gracefully if optional poses are missing
        for opt in ("sit", "groom", "lie"):
            if not self.frames[opt]:
                self.frames[opt] = self.frames["stand"][:1]
        if not self.frames["sleep"]:
            self.frames["sleep"] = self.frames["stand"][:1]

        # ---- screen / world --------------------------------------------
        geo = QApplication.primaryScreen().availableGeometry()
        self.min_x = geo.left() - 20
        self.max_x = geo.right() - self.W + 20
        self.floor_win_y = geo.bottom() - self.H + 1 + 8  # window y when cat stands on the taskbar
        self.x = float(self.max_x - 120)
        self.y = float(self.floor_win_y)
        self.vx = 0.0
        self.vy = 0.0
        self.facing_left = True

        # ---- behaviour state -------------------------------------------
        self.state = "stand"
        self.state_time = 0.0
        self.state_duration = 2.0
        self.target_x = self.x
        self.cruise_speed = self.WALK_SPEED
        self.gait = "walk"
        self.walk_phase = 0.0       # 0..1 along the walk cycle
        self.pose_frame = 0         # frame index for posed (non-walk) states
        self.pose_clock = 0.0
        self.after_turn = None      # what to do after a turn finishes
        self.landing_squash = 0.0
        self.time = 0.0
        self.wandering_enabled = True

        # dragging
        self.dragging = False
        self.drag_offset = QPointF()
        self.drag_swing = 0.0
        self.drag_swing_v = 0.0
        self.last_drag_x = 0.0
        self.drag_hist = []

        # ---- pomodoro ---------------------------------------------------
        self.preset_name = "25 / 5 minutes"
        self.work_minutes, self.break_minutes = self.PRESETS[self.preset_name]
        self.mode = "Work"
        self.remaining = self.work_minutes * 60
        self.running = False
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.tick)

        # Music starts *after* the cat is on screen (see start_music_when_calm),
        # so the browser engine no longer delays startup.
        self.music = YouTubeMusic(self)
        self.music.changed.connect(self.update)
        self.show_music_controls = self.music.settings.value("ui/music_controls", "true") == "true"
        QTimer.singleShot(600, self.start_music_when_calm)

        # rest reminder
        self.work_seconds = 0
        self.next_reminder = self.REST_AFTER_SECONDS
        self.reminder_left = 0.0
        self.on_bed = False
        self.bed = CatBed()
        self.bubble = SpeechBubble()
        self.bubble.dismissed.connect(self.dismiss_reminder)

        self.move(int(self.x), int(self.y))
        self.enter("stand", random.uniform(1.0, 2.5))

        self.elapsed = QElapsedTimer()
        self.elapsed.start()
        self.anim = QTimer(self)
        self.anim.setTimerType(Qt.TimerType.PreciseTimer)
        self.anim.timeout.connect(self.step)
        self.anim.start(16)

    # ------------------------------------------------------------------
    # Assets
    # ------------------------------------------------------------------
    def load_frames(self, prefix):
        out, i = [], 1
        while True:
            p = self.sprite_dir / f"{prefix}_{i:02d}.png"
            if not p.exists():
                break
            pm = QPixmap(str(p))
            if not pm.isNull():
                out.append(pm)
            i += 1
        return out

    def load_rig(self):
        p = self.sprite_dir / "walk_rig.json"
        if not p.exists():
            return None
        rig = json.loads(p.read_text())
        if len(rig.get("hips", [])) != len(self.frames["walk"]):
            return None
        return rig

    @staticmethod
    def make_grain(size=48):
        """Small noise tile that gives drawn legs the same grainy watercolour feel."""
        rnd = random.Random(7)
        img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        for y in range(size):
            for x in range(size):
                v = rnd.random()
                if v < 0.35:
                    img.setPixelColor(x, y, QColor(40, 30, 25, int(16 * rnd.random())))
                elif v > 0.8:
                    img.setPixelColor(x, y, QColor(255, 240, 225, int(14 * rnd.random())))
        return img

    # ------------------------------------------------------------------
    # Legs: all four are drawn procedurally so the gait is coordinated
    # ------------------------------------------------------------------
    # Phase offsets follow the classic cat walk cycle (lateral sequence): paws touch
    # down left hind -> left fore -> right hind -> right fore, a quarter cycle apart,
    # so each fore paw lifts just as the hind paw on the same side lands.
    # A leg with offset o touches down at global phase (1 - o).
    # Frames half a cycle apart are mirror images, and with a duty factor of 0.66
    # at least two paws are always on the ground.
    GAITS = {
        "walk": {"offsets": {"LH": 0.00, "LF": 0.75, "RH": 0.50, "RF": 0.25},
                 "duty": 0.66, "lift_h": 6.0, "lift_f": 7.0, "curl": 40.0},
        # rotary gallop: hind pair lands close together, then the fore pair, with a
        # moment of suspension; long strides instead of quick little steps
        "gallop": {"offsets": {"LH": 0.00, "RH": 0.09, "LF": 0.50, "RF": 0.60},
                   "duty": 0.34, "lift_h": 12.0, "lift_f": 13.0, "curl": 60.0},
    }
    NEAR = ("RH", "RF")          # legs on the viewer's side, drawn in front of the body
    FAR_OFFSET = (6.0, -3.0)     # far legs attach a little forward/up -> partial overlap
    # segment lengths in sprite pixels
    # cat proportions: femur ~ tibia, long metatarsus (the 'heel' sits high and behind)
    # storybook proportions (like the reference painting): short, tapered legs that
    # stay nearly straight, with small neat paws
    THIGH, SHIN, META = 17.0, 18.0, 11.0          # hind: hip-knee-hock-paw
    HIND_STEP_BACK = 8.0                           # hind step centred behind the hip (lands ~under it)
    UPPER_ARM, FOREARM, PASTERN = 12.0, 18.0, 5.0  # fore: shoulder-elbow-wrist-paw (short)

    def gait_params(self):
        g = self.GAITS[self.gait]
        speed = abs(self.vx)
        if self.gait == "gallop":
            # stride grows with speed, so faster = longer bounds, not faster steps
            stride = max(self.STRIDE_PX, min(165.0, 50.0 + speed * 0.78))
        else:
            stride = self.STRIDE_PX
        return g, stride

    def paw_path(self, phase, base_x, ground, duty, reach, lift):
        """(x, y, swing_t or None). Planted during stance: the paw slides back exactly
        as fast as the body moves forward, so it stays put on the floor."""
        if phase < duty:
            t = phase / duty
            return base_x + reach * (1 - 2 * t), ground, None, t
        t = (phase - duty) / (1 - duty)
        ease = (1 - math.cos(math.pi * t)) / 2
        return base_x - reach + 2 * reach * ease, ground - lift * math.sin(math.pi * t), t, None

    @staticmethod
    def two_bone(root, target, l1, l2, joint_forward):
        """Two-bone IK. joint_forward=True puts the middle joint in front (a knee),
        False behind (an elbow)."""
        rx, ry = root
        tx, ty = target
        dx, dy = tx - rx, ty - ry
        d = max(1e-3, min(math.hypot(dx, dy), l1 + l2 - 0.3))
        base = math.atan2(dy, dx)
        cos_a = (l1 * l1 + d * d - l2 * l2) / (2 * l1 * d)
        a = math.acos(max(-1.0, min(1.0, cos_a)))
        cands = [(rx + l1 * math.cos(base + s * a), ry + l1 * math.sin(base + s * a)) for s in (1, -1)]
        joint = max(cands, key=lambda c: c[0]) if joint_forward else min(cands, key=lambda c: c[0])
        ang = math.atan2(ty - joint[1], tx - joint[0])
        end = (joint[0] + l2 * math.cos(ang), joint[1] + l2 * math.sin(ang))
        return joint, end

    def leg_pose(self, leg, pivot, phase, ground):
        """Joint positions for one leg: [pivot, j1, j2, paw]."""
        g, stride = self.gait_params()
        hind = leg.endswith("H")
        total = (self.THIGH + self.SHIN + self.META) if hind else (self.UPPER_ARM + self.FOREARM + self.PASTERN)
        px0, py0 = pivot
        if self.dragging or self.state == "fall":
            # dangling: legs hang loosely and paddle a little
            paddle = math.sin((phase + (0.3 if hind else 0.0)) * math.tau)
            paw = (px0 + 3 * paddle + (-4 if hind else 2), py0 + total * 0.9)
            flex = 10.0 if hind else 20.0
            stance_t, swing_t = None, None
        else:
            reach = g["duty"] * stride / 2.0
            lift = g["lift_h"] if hind else g["lift_f"]
            base_x = px0 - self.HIND_STEP_BACK if hind else px0
            x, y, swing_t, stance_t = self.paw_path(phase, base_x, ground, g["duty"], reach, lift)
            paw = (x, y)
            if hind:
                # metatarsus angle (+ = hock in front of the paw). Like a real cat the
                # hock stays BEHIND the paw while the foot is planted (lower leg slopes
                # down-forward); the heel lifts at push-off and the foot folds back in
                # the swing before reaching forward to land.
                flex = (-24 + 30 * stance_t) if stance_t is not None else \
                    (6 + (-24 - 6) * swing_t + 38 * math.sin(math.pi * swing_t))
            else:
                # wrist: nearly straight in stance, curls back during the swing (ref. frames 3/7)
                flex = (-4 + 18 * stance_t) if stance_t is not None else \
                    (14 + (-10 - 14) * swing_t + g["curl"] * math.sin(math.pi * swing_t) ** 1.4)
        f = math.radians(flex)
        tip = self.META if hind else self.PASTERN
        # j2 (hock / wrist) sits above the paw, tilted by the flex angle
        j2_target = (paw[0] + tip * math.sin(f), paw[1] - tip * math.cos(f))
        if hind:
            j1, j2 = self.two_bone(pivot, j2_target, self.THIGH, self.SHIN, joint_forward=True)
        else:
            j1, j2 = self.two_bone(pivot, j2_target, self.UPPER_ARM, self.FOREARM, joint_forward=False)
        paw = (j2[0] - tip * math.sin(f), j2[1] + tip * math.cos(f))
        return [pivot, j1, j2, paw]

    @staticmethod
    def tapered(p0, p1, w0, w1):
        """Polygon for a limb segment tapering from width w0 to w1 (ends are capped
        separately with circles, so no path booleans are needed)."""
        x0, y0 = p0
        x1, y1 = p1
        ang = math.atan2(y1 - y0, x1 - x0)
        nx, ny = -math.sin(ang), math.cos(ang)
        return QPolygonF([QPointF(x0 + nx * w0 / 2, y0 + ny * w0 / 2),
                          QPointF(x1 + nx * w1 / 2, y1 + ny * w1 / 2),
                          QPointF(x1 - nx * w1 / 2, y1 - ny * w1 / 2),
                          QPointF(x0 - nx * w0 / 2, y0 - ny * w0 / 2)])

    def leg_shapes(self, joints, hind, k, grow=0.0):
        """Draw-calls for one leg: (kind, args). grow>0 inflates it for the outline."""
        g = grow * 2
        shapes = []
        if hind:
            widths = [w * k + g for w in (27, 14, 10, 8.5)]
            for i in range(3):
                shapes.append(("poly", self.tapered(joints[i], joints[i + 1], widths[i], widths[i + 1])))
                shapes.append(("ell", (QPointF(*joints[i]), widths[i] / 2, widths[i] / 2)))
        else:
            # storybook fore leg: one smooth cone from the chest to the wrist, so the
            # elbow never pokes out behind the leg as a bump under the belly
            sh, wr = joints[0], joints[2]
            w0, w1, w2 = 17 * k + g, 10.5 * k + g, 9 * k + g
            shapes.append(("poly", self.tapered(sh, wr, w0, w1)))
            shapes.append(("poly", self.tapered(wr, joints[3], w1, w2)))
            shapes.append(("ell", (QPointF(*wr), w1 / 2, w1 / 2)))
            shapes.append(("ell", (QPointF(*sh), w0 / 2, w0 / 2)))
        # small paw: a soft little toe pointing forward, flat on the floor
        paw = joints[3]
        if hind:
            shapes.append(("ell", (QPointF(paw[0] + 1.6, paw[1] + 0.6), 4.0 * k + grow, 2.3 * k + grow)))
        else:
            shapes.append(("ell", (QPointF(paw[0] + 1.2, paw[1] - 0.6), 5.2 * k + grow, 3.4 * k + grow)))
        return shapes

    @staticmethod
    def fill_shapes(painter, shapes):
        for kind, arg in shapes:
            if kind == "poly":
                painter.drawPolygon(arg)
            else:
                painter.drawEllipse(*arg)

    def draw_leg(self, p, joints, hind, far, ground, body_mask=None):
        pivot = joints[0]
        k = 0.92 if far else 1.0
        thigh = QColor(*self.rig["thigh"])
        paw_c = QColor(*self.rig["paw"])
        body = QColor(*self.rig["body" if hind else "chest"])
        mix = lambda a, b, t: QColor(int(a.red() + (b.red() - a.red()) * t),
                                     int(a.green() + (b.green() - a.green()) * t),
                                     int(a.blue() + (b.blue() - a.blue()) * t))
        idle = self.rig.get("idle_leg")
        if idle:
            # same fur as the idling cat's painted legs (sampled by prepare_life_sprites)
            top, mid, bot = (QColor(*idle[n]) for n in ("top", "mid", "bot"))
            if far:
                dark = QColor(*idle["dark"])
                top, mid, bot = mix(dark, top, 0.25), dark, mix(dark, bot, 0.15)
        elif far:
            # inward legs: in the body's shadow -> clearly darker and a little cooler
            shade = lambda c, f: QColor(int(c.red() * f), int(c.green() * f), min(255, int(c.blue() * f * 1.05)))
            top, mid, bot = shade(thigh, 0.74), shade(thigh, 0.70), shade(paw_c, 0.72)
        else:
            # outward legs: lit and warm, melting into the body at the top
            top, mid, bot = mix(thigh, body, 0.55), mix(thigh, body, 0.20), mix(paw_c, body, 0.30)
        top_y = pivot[1] - 14
        span = max(1.0, ground - top_y)
        at = lambda y: max(0.0, min(1.0, (y - top_y) / span))
        grad = QLinearGradient(QPointF(0, top_y), QPointF(0, ground))
        if hind:
            # haunch in the coat colour (it is the rump), darkening from the knee down
            # like the painted legs in the reference art
            hock_y = joints[2][1]
            coat = mix(body, QColor(0, 0, 0), 0.30) if far else mix(body, thigh, 0.12)
            # like the painted legs: coat colour on the thigh, then a long soft blend
            # into the grey-brown lower leg (no hard 'sock' line)
            grad.setColorAt(0.0, coat)
            grad.setColorAt(at(pivot[1] + 14), coat)
            grad.setColorAt(at(pivot[1] + 27), mix(top, coat, 0.10))
            grad.setColorAt(at(hock_y), mid)
            grad.setColorAt(1.0, bot)
        else:
            if far:
                grad.setColorAt(0.0, top)
                grad.setColorAt(at(pivot[1] + 6), top)
            else:
                # the upper leg grows out of the chest in the coat colour, then blends
                # softly into the grey-brown lower leg
                grad.setColorAt(0.0, body)
                grad.setColorAt(at(pivot[1] + 10), body)
                grad.setColorAt(at(pivot[1] + 21), mix(top, body, 0.12))
            grad.setColorAt(0.70, mid)
            grad.setColorAt(1.0, bot)

        layer = QImage(p.device().size(), QImage.Format.Format_ARGB32_Premultiplied)
        layer.fill(Qt.GlobalColor.transparent)
        lp = QPainter(layer)
        lp.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        lp.setPen(Qt.PenStyle.NoPen)
        # soft darker rim like the painted edges (fades out near the body)
        rim_start = pivot[1] + (10 if hind else 6)
        rim = QLinearGradient(QPointF(0, rim_start), QPointF(0, rim_start + 14))
        rim.setColorAt(0.0, QColor(45, 35, 30, 0))
        rim.setColorAt(1.0, QColor(45, 35, 30, 80 if far else 140))
        if hind:
            # one furry haunch silhouette wrapped around hip-knee-hock (like the reference
            # sketch), with the slim metatarsus and paw below it
            shape = self.haunch_path(joints, k)
            lp.setBrush(QBrush(grad))
            lp.drawPath(shape)
            lp.setPen(QPen(QBrush(rim), 1.5))
            lp.setBrush(Qt.BrushStyle.NoBrush)
            lp.drawPath(shape)
            lp.setPen(Qt.PenStyle.NoPen)
        else:
            # fluffy upper arm (fur bulging under the chest, with little tufts at the
            # back of the elbow) that narrows into the lower leg and paw
            shape = self.fore_path(joints, k)
            if not far:
                # soft fur fringe: a faint wider halo in the same colours
                halo = QPen(QBrush(grad), 2.6)
                halo.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                lp.setOpacity(0.35)
                lp.setPen(halo)
                lp.setBrush(Qt.BrushStyle.NoBrush)
                lp.drawPath(shape)
                lp.setOpacity(1.0)
            lp.setPen(Qt.PenStyle.NoPen)
            lp.setBrush(QBrush(grad))
            lp.drawPath(shape)
            lp.setPen(QPen(QBrush(rim), 1.3))
            lp.setBrush(Qt.BrushStyle.NoBrush)
            lp.drawPath(shape)
            lp.setPen(Qt.PenStyle.NoPen)
        lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceAtop)
        lp.fillRect(layer.rect(), QBrush(self.grain))
        if body_mask is not None:
            # Near legs are joined to the body by its real outline, not by a fixed
            # height: the leg stays opaque everywhere outside the body (so it can never
            # look detached, however far it swings) and fades out just inside the
            # belly edge, so the fur blends into the painted body.
            lp.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            for shift, opacity in ((3, 0.35), (6, 0.6), (9, 1.0)):
                lp.setOpacity(opacity)
                lp.drawImage(0, -shift, body_mask)
            lp.setOpacity(1.0)
        lp.end()
        p.drawImage(0, 0, layer)

    def fore_path(self, joints, k):
        """Fore leg silhouette: a full, furry upper arm that grows out of the chest
        (wider than the leg, bulging most at the back where the elbow fur is), tapering
        to the wrist, then the short pastern and a round paw."""
        S, Wr, P = joints[0], joints[2], joints[3]
        dx, dy = Wr[0] - S[0], Wr[1] - S[1]
        L = max(1e-3, math.hypot(dx, dy))
        ux, uy = dx / L, dy / L
        nx, ny = -uy, ux                       # points to the back (the cat faces +x)
        # half-widths along the leg (t: -0.3 inside the chest ... 1.0 at the wrist)
        # (the belly line crosses the leg around t=0.35, so the fluff peaks just below it)
        back_keys = [(-0.3, 10.0), (0.2, 11.0), (0.5, 11.5), (0.76, 6.4), (1.0, 4.2)]
        front_keys = [(-0.3, 9.5), (0.3, 9.5), (0.56, 7.6), (0.82, 5.0), (1.0, 4.2)]

        def width(keys, t):
            for (t0, w0), (t1, w1) in zip(keys, keys[1:]):
                if t <= t1:
                    e = (t - t0) / (t1 - t0)
                    e = (1 - math.cos(math.pi * e)) / 2
                    return (w0 + (w1 - w0) * e) * k
            return keys[-1][1] * k

        ts = [-0.3 + 1.3 * i / 26 for i in range(27)]
        # sample index -> extra fur length on the back edge (little tufts at the elbow)
        tufts = {16: 1.0, 17: 2.4, 18: 0.6, 19: 0.8, 20: 2.0, 21: 0.4, 22: 1.0}
        back, front = [], []
        for i, t in enumerate(ts):
            cx, cy = S[0] + dx * t, S[1] + dy * t
            wb = width(back_keys, t) + tufts.get(i, 0.0) * k
            drop = 0.9 * tufts.get(i, 0.0) * k              # tufts point a little downward
            back.append(QPointF(cx + nx * wb + ux * drop, cy + ny * wb + uy * drop))
            wf = width(front_keys, t)
            front.append(QPointF(cx - nx * wf, cy - ny * wf))
        path = QPainterPath(back[0])
        for q in back[1:]:
            path.lineTo(q)
        for q in reversed(front):
            path.lineTo(q)
        path.closeSubpath()
        # slim ankle + a small flat paw (toes forward, flat on the floor - not a hoof)
        w1, w2 = 8.4 * k, 6.0 * k
        # unite piece by piece: overlapping sub-paths in one path would cancel out
        # (odd-even fill) and punch holes into the paw
        pieces = [QPainterPath() for _ in range(3)]
        pieces[0].addPolygon(self.tapered(Wr, P, w1, w2))
        pieces[0].closeSubpath()
        pieces[1].addEllipse(QPointF(*Wr), w1 / 2, w1 / 2)
        pieces[2].addEllipse(QPointF(P[0] + 1.8, P[1] + 0.9), 4.6 * k, 1.9 * k)
        for piece in pieces:
            path = path.united(piece)
        return path.simplified()

    def haunch_path(self, joints, k):
        """Outline of a hind leg as seen on a real cat: the knee and shin are hidden
        inside the furry thigh, whose back edge runs from the rump down to the hock
        (heel) and whose front edge runs from the belly past the knee to the hock.
        Below the hock only the slim metatarsus and the paw show."""
        H, K, Hk, P = [QPointF(*j) for j in joints]
        r = 15.0 * k                                   # half-width of the thigh at the hip
        A = QPointF(H.x() - r, H.y() - 8)              # rump (rear top, inside the body)
        B = QPointF(H.x() + r * 0.8, H.y() - 8)        # flank (front top, inside the body)
        Kf = QPointF(K.x() + 5 * k, K.y() + 2)         # front of the knee (stifle)
        # sturdy lower leg ending in a small, flat paw (not a hoof)
        Hf = QPointF(Hk.x() + 4.0 * k, Hk.y() - 1)      # front of the hock
        Pf = QPointF(P.x() + 3.0 * k, P.y() - 1.6)      # top of the toes
        Ph = QPointF(P.x() - 3.0 * k, P.y() - 1.6)      # back of the paw
        path = QPainterPath()
        path.moveTo(A)
        path.cubicTo(QPointF(A.x() + 6, H.y() - 15), QPointF(B.x() - 6, H.y() - 15), B)
        # front edge: flank bulges slightly forward over the knee, then the shin runs
        # back down to the front of the hock
        path.cubicTo(QPointF(B.x() + 4 * k, B.y() + 10), QPointF(Kf.x() + 2, Kf.y() - 6), Kf)
        path.cubicTo(QPointF(Kf.x(), Kf.y() + 7), QPointF(Hf.x() + 2.5, Hf.y() - 7), Hf)
        # slim metatarsus down to the paw (toes forward, soft round paw)
        path.lineTo(Pf)
        path.quadTo(QPointF(P.x() + 7.0 * k, P.y() + 1.0), QPointF(P.x() + 3.0, P.y() + 2.8))
        path.lineTo(QPointF(P.x() - 1.5, P.y() + 2.8))
        path.quadTo(QPointF(P.x() - 4.0 * k, P.y() + 2.4), Ph)
        # long fur ("britches") on the back of the thigh hangs down past the heel and
        # hides the hock's bend, like a real cat seen from the side
        ux, uy = P.x() - Hk.x(), P.y() - Hk.y()
        ul = max(1e-3, math.hypot(ux, uy))
        Mb = QPointF(Hk.x() + ux / ul * 6 - 4.2 * k, Hk.y() + uy / ul * 6)   # back of the lower leg
        D = QPointF(Hk.x() - 7.5 * k, min(Hk.y() + 3.5, P.y() - 9))       # soft point of the fur
        path.lineTo(Mb)
        path.quadTo(QPointF(Mb.x() - 3.0 * k, Mb.y() + 0.5), D)
        rear = QPainterPath(D)
        c1 = QPointF(D.x() - 3.5 * k, D.y() - 15)
        c2 = QPointF(A.x() - 4.0 * k, A.y() + 22)
        rear.cubicTo(c1, c2, A)
        path.cubicTo(c1, c2, A)
        path.closeSubpath()
        path.setFillRule(Qt.FillRule.WindingFill)
        return path.simplified()

    # px the torso rises above its painted height while walking (negative = sits lower,
    # so the belly is as close to the floor as in the idle drawings)
    STAND_TALL = -5

    def body_motion(self):
        """(bob_px, pitch_deg, stretch) of the torso for the current gait and phase."""
        ph = self.walk_phase
        if self.dragging or self.state == "fall" or self.state != "walk":
            return 0.0, 0.0, 1.0
        speed_k = min(1.0, abs(self.vx) / max(1.0, self.cruise_speed))
        if self.gait == "gallop":
            bob = 2.0 + 4.0 * (0.5 + 0.5 * math.cos(math.tau * (ph - 0.45)))   # airborne ~0.45
            pitch = -5.0 * math.sin(math.tau * (ph - 0.20))   # nose up as the fore legs reach
            # spine flex: extended in the air, gathered when the hind legs come under
            stretch = 1.0 + 0.05 * math.cos(math.tau * (ph - 0.45))
            return bob * speed_k, pitch * speed_k, 1.0 + (stretch - 1.0) * speed_k
        # walk: torso dips slightly twice per cycle when a leg passes under it
        return 0.8 * (0.5 + 0.5 * math.cos(math.tau * 2 * ph)) * speed_k, 0.0, 1.0

    def compose_walk(self, idx):
        """Walk frame = far legs, then the body, then the near legs."""
        idx %= len(self.frames["walk"])
        body = self.frames["walk"][idx]
        if self.rig is None or "shoulders" not in self.rig:
            return body
        # extra headroom on top: the torso stands a little taller so the legs can extend
        extra = max(8, self.STAND_TALL + 8)
        img = QImage(body.width(), body.height() + extra, QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        ground = float(self.rig["ground"]) - 1 + extra     # paws touch the image bottom
        hip = self.rig["hips"][idx]
        sho = self.rig["shoulders"][idx]
        bob, pitch, stretch = self.body_motion()
        # the torso rocks/flexes around its middle; legs stay attached to the moved hips/shoulders
        mid = QPointF((hip[0] + sho[0]) / 2, (hip[1] + sho[1]) / 2)
        tf = QTransform()
        tf.translate(mid.x(), mid.y() + extra - self.STAND_TALL - bob)
        tf.rotate(pitch)
        tf.scale(stretch, 1.0)
        tf.translate(-mid.x(), -mid.y())
        g = self.GAITS[self.gait]
        poses = {}
        for leg, off in g["offsets"].items():
            base = hip if leg.endswith("H") else sho
            far = leg not in self.NEAR
            bx, by = base[0] + (self.FAR_OFFSET[0] if far else 0), base[1] + (self.FAR_OFFSET[1] if far else 0)
            m = tf.map(QPointF(bx, by))
            poses[leg] = self.leg_pose(leg, (m.x(), m.y()), (self.walk_phase + off) % 1.0, ground)
        for leg in ("LH", "LF"):
            self.draw_leg(p, poses[leg], leg.endswith("H"), True, ground)
        body_img = QImage(img.size(), QImage.Format.Format_ARGB32_Premultiplied)
        body_img.fill(Qt.GlobalColor.transparent)
        bp = QPainter(body_img)
        bp.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        bp.setTransform(tf)
        bp.drawPixmap(0, 0, body)
        bp.end()
        p.drawImage(0, 0, body_img)
        for leg in ("RH", "RF"):
            self.draw_leg(p, poses[leg], leg.endswith("H"), False, ground, body_img)
        p.end()
        return QPixmap.fromImage(img)

    def sprite(self, name, idx, flip):
        if name == "walk":
            pm = self.compose_walk(idx)
            return pm.transformed(QTransform().scale(-1, 1)) if flip else pm
        frames = self.frames[name]
        idx %= len(frames)
        if not flip:
            return frames[idx]
        key = (name, idx)
        if key not in self.flipped_cache:
            self.flipped_cache[key] = frames[idx].transformed(QTransform().scale(-1, 1))
        return self.flipped_cache[key]

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------
    def enter(self, state, duration=0.0):
        self.state = state
        self.state_time = 0.0
        self.state_duration = duration
        self.pose_clock = 0.0
        if state == "stand":
            self.pose_frame = random.randrange(len(self.frames["stand"]))
        else:
            self.pose_frame = 0

    def cursor_local(self):
        """Cursor position relative to the cat's paws (screen coords)."""
        c = QCursor.pos()
        paw_x = self.x + self.W / 2
        paw_y = self.y + self.GROUND_Y
        return c.x() - paw_x, c.y() - paw_y

    def start_walk(self, target_x, speed=None):
        target_x = max(self.min_x, min(self.max_x, target_x))
        if abs(target_x - self.x) < 25:
            self.enter("stand", random.uniform(1.5, 3.0))
            return
        self.target_x = target_x
        self.cruise_speed = speed or self.WALK_SPEED * random.uniform(0.75, 1.25)
        self.gait = "gallop" if self.cruise_speed > 80 else "walk"
        if self.state != "walk":
            self.walk_phase = 0.0
        want_left = target_x < self.x
        if want_left != self.facing_left:
            # Real cats stop and hesitate before turning around
            self.after_turn = "walk"
            self.enter("turn", random.uniform(0.25, 0.55))
        else:
            self.enter("walk")

    def choose_next(self):
        """Weighted choice of what the cat does when it finishes an activity."""
        if self.mode == "Break":
            self.enter("lie", 1.2)
            return
        if not self.wandering_enabled:
            self.enter("sit", random.uniform(4, 8))
            return

        dx, dy = self.cursor_local()
        cursor_near = abs(dx) < 420 and -260 < dy < 80
        options = [
            ("wander", 4.0),
            ("look", 2.0),
            ("sit", 2.0),
            ("groom", 1.0),
            ("run", 0.6),
            ("investigate", 3.0 if cursor_near else 0.0),
        ]
        choice = random.choices([o[0] for o in options], [o[1] for o in options])[0]

        if choice == "wander":
            # Mostly short strolls, occasionally a long walk across the screen
            dist = random.choice([random.uniform(80, 260)] * 3 + [random.uniform(300, 700)])
            direction = -1 if random.random() < 0.5 else 1
            if self.x + direction * dist < self.min_x or self.x + direction * dist > self.max_x:
                direction = -direction
            self.start_walk(self.x + direction * dist)
        elif choice == "run":
            # zoomies: dash a good distance across the screen
            dist = random.uniform(400, 900)
            direction = -1 if self.x - dist > self.min_x and (random.random() < 0.5 or self.x + dist > self.max_x) else 1
            self.start_walk(self.x + direction * dist, self.RUN_SPEED * random.uniform(0.9, 1.1))
        elif choice == "look":
            self.enter("stand", random.uniform(2.0, 5.0))
        elif choice == "sit":
            self.enter("sit", random.uniform(5.0, 12.0))
        elif choice == "groom":
            self.enter("groom", random.uniform(3.0, 6.0))
        elif choice == "investigate":
            stop_short = 70 if dx > 0 else -70
            self.start_walk(self.x + dx - stop_short, self.WALK_SPEED * 1.1)

    def step(self):
        dt = min(self.elapsed.restart() / 1000.0, 0.05)
        self.update_reminder(dt)
        self.time += dt
        self.state_time += dt
        self.pose_clock += dt

        if self.dragging:
            self.update_drag(dt)
            self.update()
            return

        # -------- airborne (dropped by the user) --------
        if self.state == "fall":
            self.vy += self.GRAVITY * dt
            self.y += self.vy * dt
            self.x += self.vx * dt
            self.vx *= 0.98
            self.x = max(self.min_x, min(self.max_x, self.x))
            if self.y >= self.floor_win_y:
                self.y = float(self.floor_win_y)
                self.landing_squash = min(1.0, self.vy / 1400.0)
                self.vy = self.vx = 0.0
                self.enter("stand", random.uniform(0.8, 1.6))
            self.move(int(self.x), int(self.y))
            self.update()
            return

        self.landing_squash = approach(self.landing_squash, 0.0, dt * 4)

        # -------- break mode: go to sleep --------
        if self.mode == "Break" and self.state not in ("lie", "sleep", "fall"):
            self.vx = 0.0
            self.enter("lie", 1.2)
        if self.mode == "Work" and self.state == "sleep" and self.state_duration == math.inf:
            self.enter("stretch_up", 1.0)

        s = self.state
        if s == "walk":
            self.update_walk(dt)
        elif s == "turn":
            if self.state_time >= self.state_duration:
                self.facing_left = not self.facing_left
                nxt, self.after_turn = self.after_turn, None
                if nxt == "walk":
                    self.enter("walk")
                else:
                    self.enter("stand", random.uniform(1, 2))
        elif s == "stand":
            self.update_look(dt)
            if self.state_time >= self.state_duration:
                self.choose_next()
        elif s in ("sit", "groom"):
            # occasionally glance toward the cursor while sitting
            if s == "sit":
                dx, _ = self.cursor_local()
                if abs(dx) < 300 and self.pose_clock > 1.5 and random.random() < 0.01:
                    self.facing_left = dx < 0
                    self.pose_clock = 0
            if self.state_time >= self.state_duration:
                self.enter("stand", random.uniform(0.6, 1.4))
        elif s == "lie":
            # brief transition pose before curling up (only ever happens on a break)
            if self.state_time >= self.state_duration:
                self.enter("sleep", math.inf)
        elif s == "sleep":
            if self.mode == "Work":   # never sleep while working
                self.enter("stretch_up", 1.0)
        elif s == "stretch_up":
            if self.state_time >= self.state_duration:
                self.enter("sit", random.uniform(2, 4))

        self.move(int(round(self.x)), int(round(self.y)))
        self.update()

    def update_walk(self, dt):
        dist = self.target_x - self.x
        direction = -1.0 if dist < 0 else 1.0
        speed = abs(self.vx)
        running = self.gait == "gallop"
        accel = self.RUN_ACCEL if running else self.ACCEL
        decel = self.RUN_DECEL if running else self.DECEL
        # Arrive behaviour: begin slowing early enough to stop on target
        stopping_dist = speed * speed / (2 * decel)
        if abs(dist) <= stopping_dist + 1.0:
            speed = approach(speed, 0.0, decel * dt)
        else:
            speed = approach(speed, self.cruise_speed, accel * dt)
        # natural surge: a walk wobbles twice per cycle, a gallop surges once per bound
        surge = 0.10 * math.cos(math.tau * (self.walk_phase - 0.5)) if running else \
            0.06 * math.sin(self.walk_phase * math.tau * 2)
        speed_now = speed * (1.0 + surge)
        self.vx = direction * speed
        move = direction * speed_now * dt
        if abs(move) >= abs(dist) or (speed < 2.0 and abs(dist) < 4.0):
            self.x = self.target_x
            self.vx = 0.0
            # settle on a "standing" frame that matches the gait
            r = random.random()
            if r < 0.15:
                self.enter("sit", random.uniform(4, 9))
            else:
                self.enter("stand", random.uniform(1.5, 4.5))
            return
        self.x += move
        # The gait cycle advances with distance -> paws stay planted
        stride = self.gait_params()[1] * self.SPRITE_SCALE
        self.walk_phase = (self.walk_phase + abs(move) / stride) % 1.0

    def update_look(self, dt):
        """While standing, cycle through idle frames slowly (look around, tail flick)
        and turn to face the cursor if it is close."""
        if self.pose_clock > random.uniform(0.9, 2.2):
            self.pose_clock = 0.0
            n = len(self.frames["stand"])
            self.pose_frame = (self.pose_frame + random.choice([1, 1, 2, -1])) % n
        dx, dy = self.cursor_local()
        if abs(dx) < 260 and -220 < dy < 60 and (dx < 0) != self.facing_left \
                and self.state_time > 0.6 and random.random() < 0.03:
            self.after_turn = "stand"
            self.enter("turn", 0.3)

    def update_drag(self, dt):
        # pendulum swing driven by horizontal drag velocity
        drag_vx = (self.x - self.last_drag_x) / max(dt, 1e-3)
        self.last_drag_x = self.x
        self.drag_swing_v += (-drag_vx * 0.004 - self.drag_swing * 30.0) * dt
        self.drag_swing_v *= 0.90
        self.drag_swing += self.drag_swing_v
        self.drag_swing = max(-0.6, min(0.6, self.drag_swing))
        self.walk_phase = (self.walk_phase + dt * 1.8) % 1.0  # legs paddle in the air

    # ------------------------------------------------------------------
    # Pomodoro
    # ------------------------------------------------------------------
    def start_music_when_calm(self):
        # Creating the browser engine blocks the UI for about a second; do it while
        # the cat is standing still so the hitch is invisible.
        if self.dragging or self.state in ("walk", "fall", "turn"):
            QTimer.singleShot(300, self.start_music_when_calm)
            return
        self.music.start()

    def start_pause(self):
        self.running = not self.running
        self.clock.start(1000) if self.running else self.clock.stop()
        self.update()

    def tick(self):
        if self.mode == "Work" and self.running:
            self.work_seconds += 1
            if (self.work_seconds >= self.next_reminder and self.reminder_left <= 0
                    and not self.on_bed):
                self.show_rest_reminder()
        if self.remaining > 0:
            self.remaining -= 1
        if self.remaining <= 0:
            QApplication.beep()
            if self.mode == "Work":
                self.mode, self.remaining = "Break", self.break_minutes * 60
                self.music.pause()
            else:
                self.mode, self.remaining = "Work", self.work_minutes * 60
                self.music.play()
                self.end_rest()
        self.update()

    def reset_timer(self):
        self.clock.stop()
        self.running = False
        mins = self.work_minutes if self.mode == "Work" else self.MANUAL_BREAK_MINUTES
        self.remaining = mins * 60
        self.update()

    def switch_mode(self, mode):
        self.clock.stop()
        self.running = False
        self.mode = mode
        self.remaining = (self.work_minutes if mode == "Work" else self.MANUAL_BREAK_MINUTES) * 60
        self.music.play() if mode == "Work" else self.music.pause()
        if mode == "Work":
            self.end_rest()
        self.update()

    # ------------------------------------------------------------------
    # Rest reminder
    # ------------------------------------------------------------------
    @staticmethod
    def fmt_duration(sec):
        h, m = divmod(int(sec) // 60, 60)
        if h and m:
            return f"{h} h {m} min"
        if h:
            return f"{h} hour" + ("s" if h > 1 else "")
        return f"{m} min"

    def show_rest_reminder(self):
        self.bubble.text = (f"Meow! You've been working for {self.fmt_duration(self.work_seconds)}.\n"
                            "You need a rest. Drag me into my bed!")
        geo = QApplication.primaryScreen().availableGeometry()
        cat_cx = self.x + self.W / 2
        # put the bed a short walk away, on whichever side has more room
        bx = cat_cx - 330 if cat_cx > geo.center().x() else cat_cx + 330
        bx = max(geo.left() + CatBed.W / 2, min(geo.right() - CatBed.W / 2, bx))
        self.bed.place(bx, self.floor_win_y + self.GROUND_Y)
        self.bed.invite = True
        self.bed.show()
        self.reminder_left = float(self.REMINDER_SECONDS)
        self.position_bubble()
        self.bubble.show()
        self.raise_()
        self.bubble.raise_()

    def position_bubble(self):
        geo = QApplication.primaryScreen().availableGeometry()
        cat_cx = self.x + self.W / 2
        head_x = cat_cx + (-38 if self.facing_left else 38)
        bx = max(geo.left(), min(geo.right() - SpeechBubble.W, head_x - SpeechBubble.W / 2))
        by = max(geo.top(), self.y + 48 - SpeechBubble.H)
        self.bubble.move(int(bx), int(by))
        self.bubble.tail_x = head_x - bx

    def update_reminder(self, dt):
        if self.reminder_left > 0:
            self.reminder_left -= dt
            self.bubble.progress = max(0.0, self.reminder_left / self.REMINDER_SECONDS)
            self.position_bubble()
            self.bubble.update()
            if self.reminder_left <= 0:
                self.dismiss_reminder()
        if self.bed.isVisible():
            self.bed.advance(dt, self.dragging and self.paw_over_bed())

    def dismiss_reminder(self):
        """Ignored (timed out or clicked away): hide everything, ask again later."""
        self.reminder_left = 0.0
        self.bubble.hide()
        self.bed.invite = False
        if not self.on_bed:
            self.bed.hide()
        self.next_reminder = self.work_seconds + self.SNOOZE_SECONDS

    def paw_over_bed(self):
        if not self.bed.isVisible():
            return False
        bx, by = self.bed.center()
        px, py = self.x + self.W / 2, self.y + self.GROUND_Y
        return abs(px - bx) < BED_W / 2 + 20 and -170 < py - by < 40

    def settle_in_bed(self):
        bx, _ = self.bed.center()
        self.x = bx - self.W / 2
        self.y = float(self.floor_win_y)
        self.vx = self.vy = 0.0
        self.on_bed = True
        self.enter("lie", 1.2)
        self.move(int(self.x), int(self.y))

    def start_rest(self):
        """Cat dropped into the bed: 15 minutes of sleep, timer running."""
        self.reminder_left = 0.0
        self.bubble.hide()
        self.bed.invite = False
        self.work_seconds = 0
        self.next_reminder = self.REST_AFTER_SECONDS
        self.switch_mode("Break")          # 15-minute break, pauses the music
        self.running = True
        self.clock.start(1000)
        self.settle_in_bed()

    def end_rest(self):
        self.on_bed = False
        if self.reminder_left <= 0:
            self.bed.invite = False
            self.bed.hide()

    def select_preset(self, name):
        self.preset_name = name
        self.work_minutes, self.break_minutes = self.PRESETS[name]
        self.switch_mode("Work")

    def toggle_wandering(self):
        self.wandering_enabled = not self.wandering_enabled
        if not self.wandering_enabled and self.state == "walk":
            self.vx = 0.0
            self.enter("stand", 1.0)

    # ------------------------------------------------------------------
    # Mouse
    # ------------------------------------------------------------------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            btn = self.button_at(e.position())
            if btn:
                self.pressed_btn = True
                self.music_action(btn)
                return
            self.dragging = True
            self.on_bed = False   # lifted out of the bed
            self.drag_offset = e.globalPosition() - QPointF(self.x, self.y)
            self.last_drag_x = self.x
            self.drag_swing = self.drag_swing_v = 0.0
            self.drag_hist = []

    def mouseMoveEvent(self, e):
        if not self.dragging:
            hb = self.button_at(e.position())
            if hb != self.hover_btn:
                self.hover_btn = hb
                self.setCursor(Qt.CursorShape.PointingHandCursor if hb else Qt.CursorShape.ArrowCursor)
                self.update()
        if self.dragging and e.buttons() & Qt.MouseButton.LeftButton:
            p = e.globalPosition() - self.drag_offset
            self.x, self.y = p.x(), min(p.y(), self.floor_win_y)
            self.drag_hist = (self.drag_hist + [(self.time, self.x, self.y)])[-6:]
            self.move(int(self.x), int(self.y))

    def leaveEvent(self, e):
        self.hover_btn = None
        self.update()

    def mouseReleaseEvent(self, e):
        self.pressed_btn = False
        if not self.dragging:
            return
        self.dragging = False
        if self.paw_over_bed():
            if self.reminder_left > 0 or self.mode != "Break":
                self.start_rest()
            else:
                self.settle_in_bed()   # already resting: just tuck back in
            return
        # throw velocity from recent drag history
        self.vx = self.vy = 0.0
        if len(self.drag_hist) >= 2:
            (t0, x0, y0), (t1, x1, y1) = self.drag_hist[0], self.drag_hist[-1]
            if t1 - t0 > 1e-3:
                self.vx = max(-900, min(900, (x1 - x0) / (t1 - t0)))
                self.vy = max(-900, min(600, (y1 - y0) / (t1 - t0)))
        if self.vx:
            self.facing_left = self.vx < 0
        if self.y < self.floor_win_y - 2:
            self.enter("fall")
        else:
            self.vx = 0.0
            self.enter("stand", 1.2)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            btn = self.button_at(e.position())
            if btn:  # a fast second click on a music button is just another click
                self.music_action(btn)
                return
            self.start_pause()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction("Pause timer" if self.running else "Start timer", self.start_pause)
        m.addAction("Reset timer", self.reset_timer)
        a = m.addAction("Wandering cat", self.toggle_wandering)
        a.setCheckable(True)
        a.setChecked(self.wandering_enabled)
        pm = m.addMenu("Pomodoro preset")
        for name in self.PRESETS:
            act = pm.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self.preset_name)
            act.triggered.connect(lambda _=False, n=name: self.select_preset(n))
        mm = m.addMenu("Timer mode")
        mm.addAction("Work", lambda: self.switch_mode("Work"))
        mm.addAction("Break / sleep (15 min)", lambda: self.switch_mode("Break"))
        rr = m.addMenu("Rest reminder")
        info = rr.addAction(f"Worked so far: {self.fmt_duration(self.work_seconds)} "
                            f"(reminds at {self.fmt_duration(self.next_reminder)})")
        info.setEnabled(False)
        rr.addAction("Show the reminder now", self.show_rest_reminder)

        if EDITION["study"]:
            m.addSeparator()
            m.addAction("Change your study playlist..." if self.music.has_source
                        else "Add your study playlist...", self.ask_music_link)
        mu = m.addMenu("Music")
        on = mu.addAction("Music on", lambda: self.music.set_enabled(not self.music.enabled))
        on.setCheckable(True)
        on.setChecked(self.music.enabled)
        sc = mu.addAction("Show music controls", self.toggle_music_controls)
        sc.setCheckable(True)
        sc.setChecked(self.show_music_controls)
        if self.music.enabled and not self.music.has_source:
            mu.addSeparator()
            mu.addAction("Add your study playlist..." if EDITION["study"]
                         else "Add music (YouTube link)...", self.ask_music_link)
        elif self.music.enabled:
            mu.addSeparator()
            mu.addAction("Pause" if self.music.playing else "Play", self.music.toggle)
            mu.addAction("Next song", self.music.next)
            mu.addAction("Previous song", self.music.previous)
            mu.addAction("Unmute" if self.music.muted else "Mute", self.music.toggle_mute)
            mu.addAction(f"Volume up  ({self.music.volume}%)", lambda: self.music.change_volume(10))
            mu.addAction("Volume down", lambda: self.music.change_volume(-10))
            mu.addSeparator()
            shown = self.music.view is not None and self.music.view.isVisible()
            mu.addAction("Hide video" if shown else "Show video", self.music.toggle_video)
            if EDITION["study"]:
                mu.addAction("Change your study playlist...", self.ask_music_link)
                mu.addAction("Remove playlist", lambda: self.music.set_url(""))
            else:
                mu.addAction("Change music (YouTube link)...", self.ask_music_link)
                mu.addAction("Reset to default music",
                             lambda: self.music.set_url(EDITION["default_url"]))
        m.addSeparator()
        m.addAction("Exit", QApplication.quit)
        m.exec(e.globalPos())

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def current_pose(self):
        """Return (sprite name, frame index)."""
        s = self.state
        if self.dragging or s == "fall":
            return "walk", int(self.walk_phase * len(self.frames["walk"]))
        if s == "walk":
            return "walk", int(self.walk_phase * len(self.frames["walk"]))
        if s == "turn":
            return "stand", self.pose_frame
        if s == "stand":
            return "stand", self.pose_frame
        if s == "sit":
            # mostly hold the first sit pose, sometimes the alternate (head turn)
            n = len(self.frames["sit"])
            return "sit", (0 if (self.time * 0.15) % 1 < 0.7 else 1) % n
        if s == "groom":
            n = len(self.frames["groom"])
            return "groom", int(self.pose_clock * 3.0) % n
        if s == "lie":
            return "lie", 0
        if s == "stretch_up":
            return "lie", 0
        if s == "sleep":
            n = len(self.frames["sleep"])
            return "sleep", int(self.time / 1.6) % n   # slow, like breathing
        return "stand", 0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        name, idx = self.current_pose()
        pm = self.sprite(name, idx, self.facing_left)
        w = pm.width() * self.SPRITE_SCALE
        h = pm.height() * self.SPRITE_SCALE
        # crossfade between different drawings (e.g. walk -> sit) so pose changes feel
        # like one cat moving rather than a picture being swapped
        last = getattr(self, "_last_draw", None)
        if last is not None and last[0] != name and not self.dragging:
            self._fade = (last[1], last[2], last[3], self.time)
        self._last_draw = (name, pm, w, h)
        fade = getattr(self, "_fade", None)
        fade_k = 0.0
        if fade is not None:
            fade_k = 1.0 - (self.time - fade[3]) / self.POSE_FADE
            if fade_k <= 0 or self.dragging:
                self._fade, fade_k = None, 0.0
        cx, gy = self.W / 2, self.GROUND_Y
        if self.on_bed and not self.dragging:
            gy -= self.BED_SINK   # rest on the cushion, not on the floor

        # breathing / squash
        resting = name in ("sleep", "sit", "lie", "stand", "groom") and not self.dragging
        breath_rate = 0.25 if name == "sleep" else 0.45
        breath = 0.018 if name == "sleep" else 0.008
        sy = 1.0 + (breath * math.sin(self.time * math.tau * breath_rate) if resting else 0.0)
        sy -= 0.18 * self.landing_squash
        sx = 1.0 + 0.12 * self.landing_squash
        # gentle body bob while walking (two bobs per stride)
        bob = 0.0
        if name == "walk" and self.rig is None and not self.dragging and self.state != "fall":
            bob = 1.2 * abs(math.sin(self.walk_phase * math.tau * 2))

        # contact shadow (fades when lifted off the ground)
        lift = 0.0
        if self.dragging or self.state == "fall" or self.on_bed:
            lift = 1.0
        shadow_w = w * (0.70 if name != "sleep" else 0.85)
        g = QRadialGradient(QPointF(cx, gy), shadow_w / 2)
        g.setColorAt(0.0, QColor(0, 0, 0, int(70 * (1 - lift))))
        g.setColorAt(1.0, QColor(0, 0, 0, 0))
        p.save()
        p.translate(cx, gy)
        p.scale(1.0, 0.16)
        p.translate(-cx, -gy)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, gy), shadow_w / 2, shadow_w / 2)
        p.restore()

        p.save()
        if self.dragging:
            # hang from the scruff (top-centre) and swing
            pivot = QPointF(cx, gy - h * 0.95)
            p.translate(pivot)
            p.rotate(math.degrees(self.drag_swing))
            p.translate(-pivot)
        p.translate(cx, gy)
        p.scale(sx, sy)
        if fade_k > 0:
            opm, ow, oh = fade[0], fade[1], fade[2]
            p.setOpacity(fade_k)
            p.drawPixmap(QRectF(-ow / 2, -oh, ow, oh), opm, QRectF(opm.rect()))
            p.setOpacity(min(1.0, 1.0 - fade_k * 0.75))
        p.drawPixmap(QRectF(-w / 2, -h - bob, w, h), pm, QRectF(pm.rect()))
        p.setOpacity(1.0)
        p.restore()
        if self.on_bed and not self.dragging:
            draw_cat_bed(p, cx, gy + self.BED_SINK, "front")

        self.paint_badge(p)
        p.end()

    BADGE = QRectF(30, 196, 180, 94)
    POSE_FADE = 0.28     # seconds to crossfade between different drawings

    def music_ui(self):
        """'full' = buttons + song title, 'hint' = one line of text, 'none' = hidden."""
        m = self.music
        if not self.show_music_controls:
            return "none"
        return "full" if (m.enabled and m.has_source) else "hint"

    def badge_rect(self):
        h = {"full": 94, "hint": 64, "none": 44}[self.music_ui()]
        return QRectF(self.BADGE.x(), self.BADGE.y(), self.BADGE.width(), h)

    def toggle_music_controls(self):
        self.show_music_controls = not self.show_music_controls
        self.music.settings.setValue("ui/music_controls", "true" if self.show_music_controls else "false")
        self.hover_btn = None
        self.update()
    BUTTONS = ("prev", "play", "next", "mute", "vol_down", "vol_up")

    def button_rects(self):
        size, gap = 24.0, 5.0
        total = len(self.BUTTONS) * size + (len(self.BUTTONS) - 1) * gap
        x0 = self.BADGE.center().x() - total / 2
        y = self.BADGE.y() + 47
        return {name: QRectF(x0 + i * (size + gap), y, size, 22)
                for i, name in enumerate(self.BUTTONS)}

    def button_at(self, pos):
        if self.music_ui() != "full":
            return None
        for name, r in self.button_rects().items():
            if r.contains(pos):
                return name
        return None

    def music_action(self, name):
        m = self.music
        if not m.has_source:
            self.ask_music_link()
            return
        if m.view is None:
            m.start()
        {"prev": m.previous, "play": m.toggle, "next": m.next, "mute": m.toggle_mute,
         "vol_down": lambda: m.change_volume(-10),
         "vol_up": lambda: m.change_volume(10)}[name]()
        self.update()

    def ask_music_link(self):
        study = EDITION["study"]
        title = "Your study playlist" if study else "Change music"
        prompt = ("Paste a YouTube playlist (or video) link for studying:" if study
                  else "Paste a YouTube video or playlist link:")
        text, ok = QInputDialog.getText(self, title, prompt, text=self.music.url)
        if not ok or not text.strip():
            return
        if parse_youtube_link(text) is None:
            QMessageBox.warning(self, title, "That doesn't look like a YouTube link.")
            return
        if not self.music.enabled:
            self.music.set_enabled(True)
        self.music.set_url(text.strip())

    def draw_icon(self, p, name, r, color):
        c = r.center()
        x, y = c.x(), c.y()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)

        def tri(pts):
            p.drawPolygon(QPolygonF([QPointF(x + a, y + b) for a, b in pts]))

        if name == "play":
            if self.music.playing:
                p.drawRect(QRectF(x - 5, y - 6, 3.5, 12))
                p.drawRect(QRectF(x + 1.5, y - 6, 3.5, 12))
            else:
                tri([(-4, -6.5), (-4, 6.5), (6.5, 0)])
        elif name == "next":
            tri([(-6, -5.5), (-6, 5.5), (2, 0)])
            p.drawRect(QRectF(x + 2.5, y - 5.5, 2.5, 11))
        elif name == "prev":
            tri([(6, -5.5), (6, 5.5), (-2, 0)])
            p.drawRect(QRectF(x - 5, y - 5.5, 2.5, 11))
        else:  # speaker-based icons
            p.drawRect(QRectF(x - 7, y - 3, 3.5, 6))
            tri([(-4, -3), (-4, 3), (1, 7), (1, -7)])
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(color, 1.4))
            if name == "mute":
                if self.music.muted:
                    p.drawLine(QPointF(x + 3, y - 3), QPointF(x + 8, y + 3))
                    p.drawLine(QPointF(x + 8, y - 3), QPointF(x + 3, y + 3))
                else:
                    p.drawArc(QRectF(x - 2, y - 4, 7, 8), -60 * 16, 120 * 16)
                    p.drawArc(QRectF(x - 3, y - 7, 11, 14), -60 * 16, 120 * 16)
            elif name == "vol_down":
                p.drawLine(QPointF(x + 3.5, y), QPointF(x + 8.5, y))
            elif name == "vol_up":
                p.drawLine(QPointF(x + 3.5, y), QPointF(x + 8.5, y))
                p.drawLine(QPointF(x + 6, y - 2.5), QPointF(x + 6, y + 2.5))

    def paint_badge(self, p):
        mm, ss = divmod(self.remaining, 60)
        bg = QColor("#40352f")
        bg.setAlpha(225)
        r = self.badge_rect()
        ui = self.music_ui()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, 12, 12)
        p.setPen(QColor("#ffffff"))
        p.setFont(QFont("Segoe UI", 10, QFont.Weight.Bold))
        p.drawText(QRectF(r.x(), r.y() + 3, r.width(), 20), Qt.AlignmentFlag.AlignCenter,
                   f"{self.mode}  {mm:02d}:{ss:02d}")
        status = {
            "walk": "Zoomies!" if self.gait == "gallop" else "Strolling...",
            "turn": "Hmm?", "stand": "Looking around", "sit": "Sitting",
            "groom": "Grooming", "lie": "Lying down", "sleep": "Zzz...",
            "stretch_up": "Stretching", "fall": "Wheee!",
        }.get(self.state, "")
        if self.dragging:
            status = "Mrrp! Put me down"
        elif not self.running and self.state not in ("sleep",):
            status = "Double-click to start"
        p.setFont(QFont("Segoe UI", 8))
        p.drawText(QRectF(r.x(), r.y() + 23, r.width(), 18), Qt.AlignmentFlag.AlignCenter, status)

        # ---- music controls (only when there is music to control) ----
        if ui == "none":
            return
        p.setPen(QColor(255, 255, 255, 40))
        p.drawLine(QPointF(r.x() + 12, r.y() + 43), QPointF(r.right() - 12, r.y() + 43))
        m = self.music
        if ui == "full":
            for name, br in self.button_rects().items():
                if name == self.hover_btn:
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(255, 255, 255, 45))
                    p.drawRoundedRect(br, 6, 6)
                col = QColor(255, 255, 255, 235 if m.ready else 90)
                self.draw_icon(p, name, br, col)

        # song title (scrolls when it is too long to fit)
        if not m.enabled:
            title = "Music off (right-click > Music)"
        elif not m.has_source:
            title = ("Right-click: add study playlist" if EDITION["study"]
                     else "Right-click to add music")
        elif m.error and not m.ready:
            title = f"Music unavailable ({m.error})"
        elif not m.ready:
            title = "Loading music..."
        else:
            title = m.title or "..."
            if m.track[1] > 1:
                title = f"{m.track[0]}/{m.track[1]}  {title}"
            if m.muted:
                title = "[muted]  " + title
        font = QFont("Segoe UI", 8)
        p.setFont(font)
        p.setPen(QColor(255, 236, 210, 220))
        tr = QRectF(r.x() + 10, r.y() + (72 if ui == "full" else 45), r.width() - 20, 16)
        tw = QFontMetrics(font).horizontalAdvance(title)
        p.save()
        p.setClipRect(tr)
        if tw <= tr.width():
            p.drawText(tr, Qt.AlignmentFlag.AlignCenter, title)
        else:
            gap = 40
            off = (self.time * 28) % (tw + gap)
            for k in (0, 1):
                p.drawText(QRectF(tr.x() - off + k * (tw + gap), tr.y(), tw + 2, tr.height()),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, title)
        p.restore()

    def closeEvent(self, e):
        self.anim.stop()
        self.clock.stop()
        self.music.shutdown()
        self.bubble.close()
        self.bed.close()
        e.accept()
        QApplication.quit()


def main():
    # Lets Qt WebEngine be imported later (lazily) instead of before the app starts.
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    app.setApplicationName(EDITION["title"])
    app.setQuitOnLastWindowClosed(False)  # hiding the video window must not quit
    cat = LifelikeCat()
    cat.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
