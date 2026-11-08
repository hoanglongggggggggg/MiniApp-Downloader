import json
import os
import sys
import time
import traceback

import requests
import urllib3
from PySide6.QtCore import (
    QBuffer, QByteArray, QEvent, QIODevice, QObject, QRunnable, QSize, Qt,
    QThreadPool, QTimer, Signal,
)
from PySide6.QtGui import (
    QColor, QFont, QIcon, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

urllib3.disable_warnings()

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
ASSETS_DIR = os.path.join(HERE, "assets")
APP_LOGO_PATH = os.path.join(ASSETS_DIR, "logo.png")

from miniapp_client import Client, download_episode, load_cookie, parse_ranges
from sources import SOURCES
from favorites import Favorites


IMG_REFERER = "https://tmaservice.developer.toutiao.com/"
CARD_WIDTH = 184
CARD_HEIGHT = 306
GRID_GAP = 16

ICONS = {}
icons_path = os.path.join(HERE, "icons.json")
if os.path.exists(icons_path):
    with open(icons_path, encoding="utf-8") as icons_file:
        ICONS = json.load(icons_file)

ICON_DIR = os.path.join(HERE, "assets", "icons")


def local_icon(app_id):
    for ext in ("png", "jpg", "jpeg", "webp"):
        path = os.path.join(ICON_DIR, f"{app_id}.{ext}")
        if os.path.exists(path):
            return path
    return None


pool = QThreadPool.globalInstance()
img_pool = QThreadPool()
img_pool.setMaxThreadCount(6)
_active = set()


class Sig(QObject):
    ok = Signal(object)
    err = Signal(str)


class Job(QRunnable):
    def __init__(self, fn, sig):
        super().__init__()
        self.fn = fn
        self.s = sig

    def run(self):
        try:
            result = self.fn()
            succeeded = True
        except Exception as exc:
            traceback.print_exc()
            result = str(exc)
            succeeded = False
        try:
            (self.s.ok if succeeded else self.s.err).emit(result)
        except RuntimeError:
            pass


def run(fn, ok=None, err=None, on_pool=None):
    sig = Sig()
    job = Job(fn, sig)
    _active.add(job)

    def clean(*_):
        _active.discard(job)

    if ok:
        sig.ok.connect(ok)
    if err:
        sig.err.connect(err)
    sig.ok.connect(clean)
    sig.err.connect(clean)
    (on_pool or pool).start(job)
    return job


def run_img(fn, ok=None, err=None):
    return run(fn, ok=ok, err=err, on_pool=img_pool)


THUMB_DIR = os.path.join(HERE, "thumb_cache")
THUMB_MAX = 480
IMG_TIMEOUT = (5, 8)

_img_fail = {}

_imgsession = requests.Session()
_imgsession.headers.update({"Referer": IMG_REFERER, "User-Agent": "Mozilla/5.0"})
_imgsession.mount("https://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16))
_imgsession.mount("http://", requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16))

_imgcache = {}


def _img_host(url):
    try:
        return url.split("://", 1)[-1].split("/", 1)[0].lower()
    except Exception:
        return ""


def _thumb_path(url):
    import hashlib
    return os.path.join(THUMB_DIR, hashlib.sha1(url.encode("utf-8")).hexdigest() + ".jpg")


def _shrink(raw):
    try:
        image = QImage()
        if not image.loadFromData(raw):
            return raw
        if max(image.width(), image.height()) > THUMB_MAX:
            image = image.scaled(THUMB_MAX, THUMB_MAX, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        buffer_data = QByteArray()
        buffer = QBuffer(buffer_data)
        buffer.open(QIODevice.WriteOnly)
        if not image.save(buffer, "JPG", 86):
            return raw
        out = bytes(buffer_data)
        return out if out else raw
    except Exception:
        return raw


def fetch_img(url):
    if not url:
        return None
    if url in _imgcache:
        return _imgcache[url]
    host = _img_host(url)
    if _img_fail.get(host, 0) >= 4:
        _imgcache[url] = None
        return None
    path = _thumb_path(url)
    try:
        if os.path.exists(path):
            with open(path, "rb") as handle:
                data = handle.read()
            if data:
                _imgcache[url] = data
                return data
    except Exception:
        pass
    try:
        response = _imgsession.get(url, timeout=IMG_TIMEOUT, verify=False)
        if response.status_code == 200 and response.content:
            data = _shrink(response.content)
            _img_fail[host] = 0
            _imgcache[url] = data
            try:
                os.makedirs(THUMB_DIR, exist_ok=True)
                with open(path, "wb") as handle:
                    handle.write(data)
            except Exception:
                pass
            return data
        _img_fail[host] = _img_fail.get(host, 0) + 1
        _imgcache[url] = None
    except Exception:
        _img_fail[host] = _img_fail.get(host, 0) + 1
        _imgcache[url] = None
    return _imgcache[url]


_logo_cache = {}


def source_logo(app_id):
    if app_id not in _logo_cache:
        path = local_icon(app_id)
        _logo_cache[app_id] = QPixmap(path) if path else None
    return _logo_cache[app_id]


def placeholder(text, width=72, height=None, radius=14):
    height = height or width
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, width, height, radius, radius)
    painter.fillPath(path, QColor("#1B1F24"))

    duos = [
        ("#6D8FB8", "#3F5D82"), ("#7E9B91", "#4E6E63"), ("#A98F73", "#7A5F45"),
        ("#8F8CA8", "#5E5B78"), ("#B08498", "#7C5468"), ("#6FA3A0", "#436E6B"),
    ]
    c_top, c_bot = duos[sum(map(ord, text or "?")) % len(duos)]
    circle_size = min(width, height) * 0.86
    circle_x = (width - circle_size) / 2
    circle_y = (height - circle_size) / 2
    grad = QLinearGradient(circle_x, circle_y, circle_x, circle_y + circle_size)
    grad.setColorAt(0.0, QColor(c_top))
    grad.setColorAt(1.0, QColor(c_bot))
    painter.setPen(Qt.NoPen)
    painter.setBrush(grad)
    painter.drawRoundedRect(circle_x, circle_y, circle_size, circle_size,
                            circle_size * 0.28, circle_size * 0.28)

    glyph = (text or "?").strip()[:1] or "?"
    painter.setPen(QColor("#FFFFFF"))
    font = QFont("Segoe UI")
    font.setPixelSize(max(16, int(circle_size * 0.5)))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, glyph)
    painter.end()
    return pixmap


_LOGO_DUOS = [
    ("#6D8FB8", "#3F5D82"), ("#7E9B91", "#4E6E63"), ("#A98F73", "#7A5F45"),
    ("#8F8CA8", "#5E5B78"), ("#B08498", "#7C5468"), ("#6FA3A0", "#436E6B"),
]


def logo_tile(text, logo, width=170, height=220, radius=14):
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setRenderHint(QPainter.SmoothPixmapTransform)
    c_top, c_bot = _LOGO_DUOS[sum(map(ord, text or "?")) % len(_LOGO_DUOS)]
    grad = QLinearGradient(0, 0, 0, height)
    grad.setColorAt(0.0, QColor(c_top))
    grad.setColorAt(1.0, QColor(c_bot))
    bg = QPainterPath()
    bg.addRoundedRect(0, 0, width, height, radius, radius)
    painter.fillPath(bg, grad)
    if logo is not None and not logo.isNull():
        s = int(min(width, height) * 0.52)
        scaled = logo.scaled(s, s, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        lx, ly = (width - s) // 2, (height - s) // 2
        clip = QPainterPath()
        clip.addRoundedRect(lx, ly, s, s, s * 0.24, s * 0.24)
        painter.save()
        painter.setClipPath(clip)
        painter.drawPixmap(lx, ly, s, s, scaled)
        painter.restore()
    painter.end()
    return pixmap


def crop_rounded(pixmap, width, height, radius=14):
    if pixmap is None or pixmap.isNull():
        return None
    scaled = pixmap.scaled(
        width,
        height,
        Qt.KeepAspectRatioByExpanding,
        Qt.SmoothTransformation,
    )
    output = QPixmap(width, height)
    output.fill(Qt.transparent)
    painter = QPainter(output)
    painter.setRenderHint(QPainter.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(0, 0, width, height, radius, radius)
    painter.setClipPath(path)
    x = (width - scaled.width()) // 2
    y = (height - scaled.height()) // 2
    painter.drawPixmap(x, y, scaled)
    painter.end()
    return output


def refresh_style(widget):
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


class SourceCard(QFrame):
    def __init__(self, src, on_click):
        super().__init__()
        self.src = src
        self._on_click = on_click
        self.setObjectName("sourceCard")
        self.setProperty("selected", False)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(72)
        self.setToolTip(src["app_id"])

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(11)

        self.icon = QLabel()
        self.icon.setObjectName("sourceIcon")
        self.icon.setFixedSize(48, 48)
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setPixmap(placeholder(src["name"], 48, 48, 12))

        copy = QVBoxLayout()
        copy.setSpacing(3)
        copy.setContentsMargins(0, 2, 0, 2)
        name = QLabel(src["name"])
        name.setObjectName("sourceName")
        name.setToolTip(src["name"])
        meta_text = "TÍCH HỢP" if src["status"].startswith("builtin") else "TÙY CHỈNH"
        if src.get("paywall"):
            meta_text += "  ·  TRẢ PHÍ"
            self.setToolTip(src["app_id"] + chr(10)*2 + "TRẢ PHÍ — " + src["paywall"])
        meta = QLabel(meta_text)
        meta.setObjectName("sourceMeta")
        if src.get("paywall"):
            meta.setProperty("paywall", True)
        copy.addWidget(name)
        copy.addWidget(meta)

        arrow = QLabel("›")
        arrow.setObjectName("sourceArrow")
        arrow.setAlignment(Qt.AlignCenter)
        arrow.setFixedWidth(12)

        row.addWidget(self.icon)
        row.addLayout(copy, 1)
        row.addWidget(arrow)

        local = local_icon(src["app_id"])
        if local:
            artwork = crop_rounded(QPixmap(local), 48, 48, 12)
            if artwork:
                self.icon.setPixmap(artwork)
        else:
            icon_meta = ICONS.get(src["app_id"])
            if icon_meta and icon_meta.get("icon"):
                run_img(lambda url=icon_meta["icon"]: fetch_img(url), ok=self._set_icon)

    def set_selected(self, selected):
        self.setProperty("selected", selected)
        refresh_style(self)

    def _set_icon(self, data):
        if not data:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        artwork = crop_rounded(pixmap, 48, 48, 12)
        if artwork:
            self.icon.setPixmap(artwork)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click(self.src, self)
        super().mousePressEvent(event)


class DramaCard(QFrame):
    def __init__(self, item, src, on_open, favs=None, on_fav=None):
        super().__init__()
        self.setObjectName("dramaCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(CARD_WIDTH, CARD_HEIGHT)

        info = item.get("collection_info", {}) or {}
        self.cid = item.get("id") or info.get("collection_id")
        self.src = src
        self._on_open = on_open
        self.favs = favs
        self._on_fav = on_fav
        self._cover_url = ""
        self._eps = info.get("collection_updated_num")
        self.name = item.get("name") or "Chưa có tiêu đề"
        self.setToolTip(f"{self.name}\nID: {self.cid}")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(7, 7, 7, 10)
        layout.setSpacing(8)

        self.cover = QLabel()
        self.cover.setObjectName("poster")
        self.cover.setFixedSize(170, 220)
        self.cover.setAlignment(Qt.AlignCenter)
        logo = source_logo(src.get("app_id"))
        if logo is not None:
            self.cover.setPixmap(logo_tile(self.name, logo, 170, 220, 12))
        else:
            self.cover.setPixmap(placeholder(self.name, 170, 220, 12))

        name = QLabel(self.name)
        name.setObjectName("dramaName")
        name.setWordWrap(True)
        name.setFixedHeight(36)

        episodes = info.get("collection_updated_num")
        watch = item.get("watch_num")
        if episodes not in (None, ""):
            detail = f"{episodes} tập"
        elif watch:
            detail = f"{int(watch):,}".replace(",", ".") + " lượt xem"
        else:
            detail = "Đang cập nhật"
        meta = QLabel(f"{src['name']}  •  {detail}")
        meta.setObjectName("dramaMeta")
        meta.setToolTip(f"ID: {self.cid}")

        layout.addWidget(self.cover)
        layout.addWidget(name)
        layout.addWidget(meta)

        thumbnails = item.get("thumbnails") or []
        fallback_cover = thumbnails[0] if isinstance(thumbnails, list) and thumbnails else None
        cover_url = item.get("cover_url") or fallback_cover
        self._cover_url = cover_url or ""
        if cover_url:
            run_img(lambda url=cover_url: fetch_img(url), ok=self._set_cover)

        if self.favs is not None:
            self.heart = QPushButton(self)
            self.heart.setObjectName("heartBtn")
            self.heart.setCursor(Qt.PointingHandCursor)
            self.heart.setFixedSize(30, 30)
            self.heart.move(CARD_WIDTH - 38, 14)
            self.heart.clicked.connect(self._toggle_fav)
            self._refresh_heart()
            self.heart.raise_()

    def _fav_entry(self):
        return Favorites.entry_from_src(self.src, self.cid, self.name,
                                        cover_url=self._cover_url, eps=self._eps)

    def _refresh_heart(self):
        if self.favs is None:
            return
        on = self.favs.is_fav(self.src.get("app_id"), self.cid, self.src.get("project_id"))
        self.heart.setText("♥" if on else "♡")
        self.heart.setProperty("on", on)
        self.heart.setToolTip("Bỏ lưu" if on else "Lưu bộ này")
        refresh_style(self.heart)

    def _toggle_fav(self):
        if self.favs is None:
            return
        self.favs.toggle(self._fav_entry())
        self._refresh_heart()
        if self._on_fav:
            self._on_fav()

    def _set_cover(self, data):
        if not data:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        artwork = crop_rounded(pixmap, 170, 220, 12)
        if artwork:
            self.cover.setPixmap(artwork)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_open(self.src, self.cid, self.name)
        super().mousePressEvent(event)


class EmptyState(QFrame):
    def __init__(self, title, description):
        super().__init__()
        self.setObjectName("emptyState")
        self.setMinimumHeight(330)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 56, 32, 56)
        layout.setAlignment(Qt.AlignCenter)
        layout.setSpacing(10)

        symbol = QLabel("◇")
        symbol.setObjectName("emptySymbol")
        symbol.setAlignment(Qt.AlignCenter)
        symbol.setFixedSize(56, 56)
        heading = QLabel(title)
        heading.setObjectName("emptyTitle")
        heading.setAlignment(Qt.AlignCenter)
        body = QLabel(description)
        body.setObjectName("emptyDescription")
        body.setAlignment(Qt.AlignCenter)
        body.setWordWrap(True)
        body.setMaximumWidth(460)

        layout.addWidget(symbol, 0, Qt.AlignHCenter)
        layout.addWidget(heading)
        layout.addWidget(body)


class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("MiniappVIP — Content Workspace")
        if os.path.exists(APP_LOGO_PATH):
            self.setWindowIcon(QIcon(APP_LOGO_PATH))
        self.resize(1280, 820)
        self.setMinimumSize(980, 660)

        token = ""
        token_path = os.path.join(HERE, "token.txt")
        if os.path.exists(token_path):
            with open(token_path, encoding="utf-8") as token_file:
                token = token_file.read().strip()

        self.cli = Client(
            cookie=load_cookie(os.path.join(HERE, "cookie.local.txt")),
            token=token,
        )
        self.favs = Favorites(os.path.join(HERE, "favorites.local.json"))
        self.outdir = os.path.join(HERE, "downloads")
        self.source_cards = []
        self.active_source_card = None
        self.cards = []
        self.empty_widget = None
        self.dialogs = []
        self._last_columns = 0
        self.current_source = None
        self.current_mode = "new"
        self._showing_favs = False
        self._page = 1
        self._shown_ids = set()
        self._loading_more = False
        self.tab_buttons = {}

        central = QWidget()
        central.setObjectName("appRoot")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())

        body = QWidget()
        body.setObjectName("body")
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        body_layout.addWidget(self._build_sidebar())
        body_layout.addWidget(self._build_content(), 1)
        root.addWidget(body, 1)

        self.apply_style()
        self.show_empty(
            "Chọn một nguồn để bắt đầu",
            "Chọn một app ở thanh bên để duyệt phim đề xuất, rồi nhấp poster để mở playlist và tải tập.",
        )
        self._update_auth(token)

    def _build_topbar(self):
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar.setFixedHeight(88)
        row = QHBoxLayout(topbar)
        row.setContentsMargins(24, 16, 24, 16)
        row.setSpacing(18)

        brand = QWidget()
        brand.setObjectName("brand")
        brand.setFixedWidth(228)
        brand_row = QHBoxLayout(brand)
        brand_row.setContentsMargins(0, 0, 0, 0)
        brand_row.setSpacing(12)
        mark = QLabel()
        mark.setObjectName("brandMark")
        mark.setAlignment(Qt.AlignCenter)
        mark.setFixedSize(50, 50)
        if os.path.exists(APP_LOGO_PATH):
            logo = QPixmap(APP_LOGO_PATH)
            mark.setPixmap(logo.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            mark.setToolTip("Miniapp Content Workspace")
        else:
            mark.setText("M")
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        brand_title = QLabel("MINIAPP")
        brand_title.setObjectName("brandTitle")
        brand_subtitle = QLabel("CONTENT WORKSPACE")
        brand_subtitle.setObjectName("brandSubtitle")
        brand_copy.addWidget(brand_title)
        brand_copy.addWidget(brand_subtitle)
        brand_row.addWidget(mark)
        brand_row.addLayout(brand_copy)

        self.auth_label = QLabel()
        self.auth_label.setObjectName("authBadge")
        self.auth_label.setAlignment(Qt.AlignCenter)
        self.auth_label.setMinimumWidth(126)
        self.auth_label.setToolTip("Trạng thái xác thực hiện tại")

        self.token_button = QPushButton("Kiểm tra cookie")
        self.token_button.setObjectName("ghostButton")
        self.token_button.setCursor(Qt.PointingHandCursor)
        self.token_button.clicked.connect(self.get_token)

        row.addWidget(brand)
        row.addStretch(1)
        row.addWidget(self.auth_label)
        row.addWidget(self.token_button)
        return topbar

    def _build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(252)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(16, 24, 16, 18)
        layout.setSpacing(12)

        self.fav_button = QPushButton()
        self.fav_button.setObjectName("favNavButton")
        self.fav_button.setCursor(Qt.PointingHandCursor)
        self.fav_button.clicked.connect(self.show_favorites)
        self._refresh_fav_button()
        layout.addWidget(self.fav_button)

        eyebrow = QLabel("NGUỒN NỘI DUNG")
        eyebrow.setObjectName("sidebarEyebrow")
        source_list = [
            source
            for source in SOURCES
            if source["adapter"] == "duolai"
        ]
        count = QLabel(f"{len(source_list):02d} nguồn đang hoạt động")
        count.setObjectName("sidebarCount")
        layout.addWidget(eyebrow)
        layout.addWidget(count)

        scroll = QScrollArea()
        scroll.setObjectName("sourceScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        source_host = QWidget()
        source_host.setObjectName("sourceHost")
        source_layout = QVBoxLayout(source_host)
        source_layout.setContentsMargins(0, 4, 4, 4)
        source_layout.setSpacing(8)
        for source in source_list:
            card = SourceCard(source, self.browse_app)
            self.source_cards.append(card)
            source_layout.addWidget(card)
        source_layout.addStretch(1)
        scroll.setWidget(source_host)
        layout.addWidget(scroll, 1)

        hint = QFrame()
        hint.setObjectName("sidebarHint")
        hint_layout = QVBoxLayout(hint)
        hint_layout.setContentsMargins(12, 10, 12, 10)
        hint_layout.setSpacing(2)
        hint_title = QLabel("MẸO NHANH")
        hint_title.setObjectName("hintTitle")
        hint_body = QLabel("Nhấp poster để mở playlist • Bấm ♥ để lưu bộ vào thư viện")
        hint_body.setObjectName("hintBody")
        hint_body.setWordWrap(True)
        hint_layout.addWidget(hint_title)
        hint_layout.addWidget(hint_body)
        layout.addWidget(hint)
        return sidebar

    def _build_content(self):
        content = QFrame()
        content.setObjectName("content")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 22, 28, 20)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)
        title_copy = QVBoxLayout()
        title_copy.setSpacing(3)
        self.section_eyebrow = QLabel("THƯ VIỆN PHIM")
        self.section_eyebrow.setObjectName("sectionEyebrow")
        self.page_title = QLabel("Khám phá nội dung")
        self.page_title.setObjectName("pageTitle")
        title_copy.addWidget(self.section_eyebrow)
        title_copy.addWidget(self.page_title)
        self.result_badge = QLabel("SẴN SÀNG")
        self.result_badge.setObjectName("resultBadge")
        self.result_badge.setAlignment(Qt.AlignCenter)
        header.addLayout(title_copy)
        header.addStretch(1)
        header.addWidget(self.result_badge)
        layout.addLayout(header)

        self.tab_bar = self._build_tabs()
        self.tab_bar.setVisible(False)
        layout.addSpacing(14)
        layout.addWidget(self.tab_bar)

        self.status = QLabel("Chọn một nguồn để nạp dữ liệu.")
        self.status.setObjectName("contentStatus")
        self.status.setProperty("statusType", "muted")
        self.status.setMinimumHeight(34)
        layout.addWidget(self.status)

        self.activity = QProgressBar()
        self.activity.setObjectName("activityBar")
        self.activity.setRange(0, 0)
        self.activity.setTextVisible(False)
        self.activity.setFixedHeight(2)
        self.activity.hide()
        layout.addWidget(self.activity)
        layout.addSpacing(12)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("contentScroll")
        self.scroll.setWidgetResizable(True)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("gridHost")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 8, 8)
        self.grid.setHorizontalSpacing(GRID_GAP)
        self.grid.setVerticalSpacing(GRID_GAP)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.grid_host)
        layout.addWidget(self.scroll, 1)

        self.more_btn = QPushButton("Tải thêm")
        self.more_btn.setObjectName("secondaryButton")
        self.more_btn.setCursor(Qt.PointingHandCursor)
        self.more_btn.setVisible(False)
        self.more_btn.clicked.connect(self.load_more)
        more_row = QHBoxLayout()
        more_row.addStretch(1)
        more_row.addWidget(self.more_btn)
        more_row.addStretch(1)
        layout.addLayout(more_row)
        return content

    def _build_tabs(self):
        tabs = [
            ("Đề xuất", "new", "Phim mới · ưu tiên ít lượt xem"),
            ("Đang hot", "hot", "Xếp theo lượt xem"),
            ("Nổi bật", "banner", "Banner và lựa chọn của app"),
            ("Miễn phí", "free", "Feed xem quảng cáo / giới hạn"),
            ("Thể loại", "category", "Lọc theo từng app"),
        ]
        bar = QFrame()
        bar.setObjectName("tabBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        for label, mode, subtitle in tabs:
            tab = QFrame()
            tab.setObjectName("discoverTab")
            tab.setProperty("selected", mode == self.current_mode)
            tab.setCursor(Qt.PointingHandCursor)
            box = QVBoxLayout(tab)
            box.setContentsMargins(14, 10, 14, 10)
            box.setSpacing(2)
            title = QLabel(label)
            title.setObjectName("tabTitle")
            sub = QLabel(subtitle)
            sub.setObjectName("tabSubtitle")
            sub.setWordWrap(True)
            box.addWidget(title)
            box.addWidget(sub)
            tab.mousePressEvent = lambda event, m=mode: self._on_tab(m)
            self.tab_buttons[mode] = tab
            row.addWidget(tab, 1)
        return bar

    def _on_tab(self, mode):
        self.current_mode = mode
        for tab_mode, tab in self.tab_buttons.items():
            tab.setProperty("selected", tab_mode == mode)
            refresh_style(tab)
        if self.current_source:
            self.load_discover(self.current_source, mode)

    def load_discover(self, src, mode):
        labels = {"new": "Đề xuất", "hot": "Đang hot", "banner": "Nổi bật",
                  "free": "Miễn phí", "category": "Thể loại"}
        self._page = 1
        self._shown_ids = set()
        self.more_btn.setVisible(False)
        self.set_header("KHÁM PHÁ", src["name"], "ĐANG NẠP")
        self.set_status(f"Đang nạp “{labels.get(mode, mode)}” từ {src['name']}…")
        self.set_busy(True)
        run(lambda: self.cli.discover(src, mode, page=1),
            ok=lambda result: self._got_discover(result, src, mode), err=self._err)

    def _got_discover(self, result, src, mode):
        kind, items = result
        self.set_busy(False)
        if kind == "unavailable":
            self.set_header("KHÁM PHÁ", src["name"], "CHƯA CÓ API")
            self.set_status("Mục này backend duolai chưa mở (功能正在开发测试中).", "warning")
            self.show_empty(
                "Backend chưa mở API mục này",
                "Nguồn duolai chưa cung cấp API cho “Thể loại”. Dùng Đề xuất / Đang hot / Nổi bật / Miễn phí.",
            )
            return
        self.set_header("KHÁM PHÁ", src["name"], f"{len(items):02d} KẾT QUẢ")
        alt = getattr(self.cli, "borrowed_catalog", {}).get(src["app_id"])
        if alt:
            self.set_status(f"{src['name']} • {len(items)} mục • danh mục MƯỢN từ app {alt} "
                            f"(app này không có feed riêng — phim vẫn mở/tải bình thường)", "warning")
        else:
            self.set_status(f"{src['name']} • {len(items)} mục", "success")
        self._shown_ids = {it.get("id") for it in items if it.get("id") is not None}
        self.fill(items, src)
        self.more_btn.setVisible(bool(items) and mode not in ("banner", "category"))
        self.more_btn.setEnabled(True)
        self.more_btn.setText("Tải thêm")

    def load_more(self):
        if self._loading_more or not self.current_source:
            return
        self._loading_more = True
        self._page += 1
        self.more_btn.setEnabled(False)
        self.more_btn.setText("Đang nạp…")
        src, mode, page = self.current_source, self.current_mode, self._page
        run(lambda: self.cli.discover(src, mode, page=page),
            ok=lambda result: self._got_more(result, src, mode),
            err=self._more_err)

    def _got_more(self, result, src, mode):
        self._loading_more = False
        if src is not self.current_source or mode != self.current_mode:
            return
        _, items = result
        fresh = []
        for it in (items or []):
            iid = it.get("id")
            if iid is not None and iid in self._shown_ids:
                continue
            if iid is not None:
                self._shown_ids.add(iid)
            fresh.append(it)
        if fresh:
            self.append_cards([DramaCard(it, src, self.open_playlist, self.favs, self._on_fav_changed)
                               for it in fresh])
            self.set_header("KHÁM PHÁ", src["name"], f"{len(self.cards):02d} KẾT QUẢ")
            self.set_status(f"{src['name']} • {len(self.cards)} mục (trang {self._page})", "success")
            self.more_btn.setEnabled(True)
            self.more_btn.setText("Tải thêm")
        else:
            self.more_btn.setVisible(False)
            self.set_status(f"{src['name']} • đã hết phim để nạp thêm", "muted")

    def _more_err(self, message):
        self._loading_more = False
        self.more_btn.setEnabled(True)
        self.more_btn.setText("Tải thêm")
        self.set_status(f"Nạp thêm lỗi: {message}", "warning")

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "scroll"):
            QTimer.singleShot(0, self.relayout_cards)

    def _column_count(self):
        if not hasattr(self, "scroll"):
            return 1
        available = max(CARD_WIDTH, self.scroll.viewport().width() - 8)
        return max(1, (available + GRID_GAP) // (CARD_WIDTH + GRID_GAP))

    def _clear_grid(self, delete_widgets=True):
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget and delete_widgets:
                widget.deleteLater()

    def relayout_cards(self):
        if not self.cards:
            return
        columns = self._column_count()
        if columns == self._last_columns and self.grid.count() == len(self.cards):
            return
        self._clear_grid(delete_widgets=False)
        for index, card in enumerate(self.cards):
            self.grid.addWidget(card, index // columns, index % columns)
        self._last_columns = columns

    def show_empty(self, title, description):
        self._clear_grid()
        self.cards = []
        self._last_columns = 0
        self.empty_widget = EmptyState(title, description)
        self.grid.addWidget(self.empty_widget, 0, 0)

    def show_cards(self, cards):
        self._clear_grid()
        self.empty_widget = None
        self.cards = cards
        self._last_columns = 0
        if not cards:
            self.show_empty(
                "Không có kết quả phù hợp",
                "Thử chọn tab khác hoặc nguồn nội dung khác từ thanh bên.",
            )
            return
        self.relayout_cards()

    def append_cards(self, new_cards):
        if not new_cards:
            return
        if self.empty_widget is not None:
            self._clear_grid()
            self.empty_widget = None
            self.cards = []
        self.cards.extend(new_cards)
        self._last_columns = -1
        self.relayout_cards()

    def set_status(self, text, status_type="muted"):
        self.status.setText(text)
        self.status.setProperty("statusType", status_type)
        refresh_style(self.status)

    def set_busy(self, busy):
        self.activity.setVisible(busy)

    def set_header(self, eyebrow, title, badge):
        self.section_eyebrow.setText(eyebrow)
        self.page_title.setText(title)
        self.result_badge.setText(badge)

    def select_source(self, selected_card=None):
        self.active_source_card = selected_card
        for card in self.source_cards:
            card.set_selected(card is selected_card)

    def _update_auth(self, token=""):
        if self.cli.cookie:
            label, state = "● COOKIE SẴN SÀNG", "ready"
            message = "Xác thực: cookie sẵn sàng • token sẽ được tạo tự động khi tải"
        elif token:
            label, state = "● TOKEN THỦ CÔNG", "token"
            message = "Xác thực: đang dùng token thủ công"
        else:
            label, state = "● THIẾU COOKIE", "missing"
            message = "Xác thực: thiếu cookie.local.txt"
        self.auth_label.setText(label)
        self.auth_label.setProperty("authState", state)
        refresh_style(self.auth_label)
        self.statusBar().showMessage(message)

    def fill(self, items, src):
        cards = [DramaCard(item, src, self.open_playlist, self.favs, self._on_fav_changed)
                 for item in items]
        self.show_cards(cards)

    def browse_app(self, src, source_card=None):
        self._showing_favs = False
        self.select_source(source_card)
        self.current_source = src
        self.tab_bar.setVisible(True)
        self.load_discover(src, self.current_mode)

    def _refresh_fav_button(self):
        self.fav_button.setText(f"♥   Đã lưu ({len(self.favs)})")

    def _on_fav_changed(self):
        self._refresh_fav_button()
        if getattr(self, "_showing_favs", False):
            QTimer.singleShot(0, self.show_favorites)

    def note_downloaded(self, src, cid, name, cover_url="", eps=None):
        entry = Favorites.entry_from_src(src, cid, name, cover_url=cover_url, eps=eps)
        self.favs.add(entry, downloaded=True)
        self._refresh_fav_button()
        if getattr(self, "_showing_favs", False):
            QTimer.singleShot(0, self.show_favorites)

    def show_favorites(self):
        self._showing_favs = True
        self.select_source(None)
        self.current_source = None
        self.tab_bar.setVisible(False)
        self.more_btn.setVisible(False)
        items = self.favs.all()
        self.set_header("THƯ VIỆN", "Bộ đã lưu", f"{len(items):02d} BỘ")
        if not items:
            self.set_status("Chưa lưu bộ nào — bấm ♥ trên poster hoặc trong playlist để lưu.", "muted")
            self.show_empty(
                "Chưa có bộ đã lưu",
                "Bấm nút ♥ ở góc poster (hoặc trong hộp playlist) để lưu bộ cho lần sau. "
                "Bộ tải xong cũng tự xuất hiện ở đây.",
            )
            return
        cards = []
        for e in items:
            src = self.favs.src_of(e)
            item = {"id": e.get("cid"), "name": e.get("name"),
                    "cover_url": e.get("cover_url"),
                    "collection_info": {"collection_id": e.get("cid"),
                                        "collection_updated_num": e.get("eps")}}
            cards.append(DramaCard(item, src, self.open_playlist, self.favs, self._on_fav_changed))
        self.set_status(f"{len(items)} bộ đã lưu • bấm poster để mở, ♥ để bỏ lưu", "success")
        self.show_cards(cards)

    def _err(self, message):
        self.set_busy(False)
        self.result_badge.setText("CÓ LỖI")
        self.set_status("Không thể hoàn tất: " + message, "error")

    def open_playlist(self, src, cid, name):
        if not self.cli.cookie and not self.cli.token:
            QMessageBox.information(
                self,
                "Cần cookie",
                "Cần cookie.local.txt (Douyin) để tự tạo token.\n"
                "Đặt file cookie cạnh gui_qt.py rồi mở lại ứng dụng.",
            )
            return
        self.set_status(f"Đang mở playlist “{name}”…")
        self.set_busy(True)
        run(
            lambda: self.cli.playlist(src, cid),
            ok=lambda result: self._show_playlist(src, cid, name, result),
            err=self._err,
        )

    def _show_playlist(self, src, cid, name, result):
        self.set_busy(False)
        self.set_status(f"Đã mở playlist • {name}", "success")
        dialog = PlaylistDialog(self, src, cid, name, result)
        self.dialogs.append(dialog)
        dialog.finished.connect(lambda _=0, dlg=dialog: self._forget_dialog(dlg))
        dialog.show()
        run(lambda: self.cli.detail(src, cid),
            ok=lambda info, dlg=dialog: dlg.apply_detail(info) if dlg.isVisible() else None)

    def _forget_dialog(self, dialog):
        if dialog in self.dialogs:
            self.dialogs.remove(dialog)

    def get_token(self):
        self.token_button.setEnabled(False)
        self.token_button.setText("Đang kiểm tra…")

        def done(token):
            self.token_button.setEnabled(True)
            self.token_button.setText("Kiểm tra cookie")
            self._update_auth(token)
            if token:
                QMessageBox.information(
                    self,
                    "Cookie hợp lệ",
                    "Cookie hoạt động bình thường.\n\nToken vừa tạo:\n" + token,
                )
            else:
                QMessageBox.warning(
                    self,
                    "Cookie không hợp lệ",
                    "Không tạo được token. Cookie có thể đã hết hạn.",
                )

        def failed(message):
            self.token_button.setEnabled(True)
            self.token_button.setText("Kiểm tra cookie")
            self._err(message)

        run(lambda: self.cli.mint_token("tt5b4d2df0c47bdd2201"), ok=done, err=failed)

    def apply_style(self):
        self.setStyleSheet(
            """
            * {
                font-family: "Segoe UI";
                outline: none;
            }
            QMainWindow, QWidget#appRoot, QWidget#body {
                background: #090A0C;
                color: #E8EAED;
            }
            QFrame#topbar {
                background: #0E1013;
                border-bottom: 1px solid #25292F;
            }
            QWidget#brand { background: transparent; }
            QLabel#brandMark {
                background: transparent;
                color: #D7DEE6;
                font-size: 18px;
                font-weight: 800;
            }
            QLabel#brandTitle {
                color: #F1F3F5;
                font-size: 16px;
                font-weight: 750;
                letter-spacing: 1px;
            }
            QLabel#brandSubtitle, QLabel#sidebarEyebrow, QLabel#sectionEyebrow,
            QLabel#hintTitle {
                color: #7D858F;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1.5px;
            }
            QPushButton {
                min-height: 36px;
                padding: 0 14px;
                border-radius: 8px;
                font-size: 11px;
                font-weight: 650;
            }
            QPushButton#primaryButton {
                background: #C9D4DF;
                color: #101317;
                border: 1px solid #C9D4DF;
            }
            QPushButton#primaryButton:hover { background: #E0E7EE; border-color: #E0E7EE; }
            QPushButton#primaryButton:pressed { background: #AAB8C5; }
            QPushButton#ghostButton, QPushButton#secondaryButton {
                background: #16191D;
                color: #C9CDD2;
                border: 1px solid #30353C;
            }
            QPushButton#ghostButton:hover, QPushButton#secondaryButton:hover {
                background: #20242A;
                border-color: #464D56;
                color: #F3F4F6;
            }
            QPushButton:disabled {
                background: #17191C;
                color: #5F646B;
                border-color: #25282D;
            }
            QLabel#authBadge, QLabel#resultBadge {
                min-height: 28px;
                padding: 0 10px;
                border-radius: 7px;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.6px;
            }
            QLabel#authBadge[authState="ready"] {
                color: #9CC5B4;
                background: #14201C;
                border: 1px solid #28443A;
            }
            QLabel#authBadge[authState="token"] {
                color: #AFC2D5;
                background: #151D25;
                border: 1px solid #2A3C4D;
            }
            QLabel#authBadge[authState="missing"] {
                color: #D1A5A5;
                background: #241718;
                border: 1px solid #4A2B2D;
            }
            QFrame#sidebar {
                background: #0C0E11;
                border-right: 1px solid #23272C;
            }
            QLabel#sidebarCount { color: #C7CBD0; font-size: 12px; margin-bottom: 5px; }
            QScrollArea#sourceScroll, QWidget#sourceHost {
                background: transparent;
                border: none;
            }
            QFrame#sourceCard {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 11px;
            }
            QFrame#sourceCard:hover {
                background: #15181C;
                border-color: #292E34;
            }
            QFrame#sourceCard[selected="true"] {
                background: #1A1E23;
                border-color: #48535E;
            }
            QLabel#sourceIcon { background: #1B1F24; border-radius: 12px; }
            QLabel#sourceName { color: #D9DCE0; font-size: 12px; font-weight: 650; }
            QLabel#sourceMeta { color: #656D76; font-size: 8px; font-weight: 700; letter-spacing: 0.8px; }
            QLabel#sourceMeta[paywall="true"] { color: #C98B7A; }
            QLabel#sourceArrow { color: #5D6670; font-size: 19px; }
            QFrame#sourceCard[selected="true"] QLabel#sourceName { color: #F4F5F6; }
            QFrame#sourceCard[selected="true"] QLabel#sourceMeta,
            QFrame#sourceCard[selected="true"] QLabel#sourceArrow { color: #9EB2C5; }
            QFrame#sidebarHint {
                background: #111418;
                border: 1px solid #24292F;
                border-radius: 9px;
            }
            QLabel#hintBody { color: #737B84; font-size: 10px; }
            QFrame#content { background: #090A0C; }
            QLabel#pageTitle {
                color: #F2F3F4;
                font-size: 25px;
                font-weight: 700;
            }
            QLabel#resultBadge {
                color: #AEBAC6;
                background: #15191D;
                border: 1px solid #2D333A;
            }
            QLabel#contentStatus { color: #7E858D; font-size: 11px; }
            QLabel#contentStatus[statusType="success"] { color: #86AA9B; }
            QLabel#contentStatus[statusType="warning"] { color: #BCA77E; }
            QLabel#contentStatus[statusType="error"] { color: #C98F91; }
            QLabel#dialogSynopsis { color: #8D949C; font-size: 11px; margin-top: 6px; }
            QLabel#dialogWarn {
                color: #BCA77E; font-size: 11px; background: #221E17;
                border: 1px solid #3A3325; border-radius: 8px; padding: 9px 11px;
            }
            QProgressBar#activityBar { background: #171A1E; border: none; }
            QProgressBar#activityBar::chunk { background: #8DA6BD; }
            QScrollArea#contentScroll, QWidget#gridHost {
                background: transparent;
                border: none;
            }
            QFrame#dramaCard {
                background: #13161A;
                border: 1px solid #252A30;
                border-radius: 14px;
            }
            QFrame#dramaCard:hover {
                background: #181C21;
                border-color: #596674;
            }
            QLabel#poster { background: #1A1E23; border-radius: 12px; }
            QLabel#dramaName {
                color: #E4E6E9;
                font-size: 12px;
                font-weight: 650;
            }
            QLabel#dramaMeta { color: #717983; font-size: 9px; }
            QPushButton#heartBtn {
                background: rgba(9,11,14,0.70);
                color: #D2D7DD;
                border: none;
                border-radius: 15px;
                font-size: 17px;
                padding: 0;
            }
            QPushButton#heartBtn:hover { background: rgba(20,25,30,0.92); color: #FF6B81; }
            QPushButton#heartBtn[on="true"] { color: #FF4D67; }
            QPushButton#favNavButton {
                background: #1C130F;
                color: #F2A488;
                border: 1px solid #E0623F;
                border-radius: 10px;
                padding: 11px 13px;
                font-size: 12px;
                font-weight: 700;
                text-align: left;
            }
            QPushButton#favNavButton:hover { background: #24160F; border-color: #F2814F; }
            QFrame#emptyState {
                background: #0E1013;
                border: 1px dashed #2C3137;
                border-radius: 14px;
            }
            QLabel#emptySymbol {
                color: #8B9BAA;
                background: #171A1E;
                border: 1px solid #30363D;
                border-radius: 15px;
                font-size: 25px;
            }
            QLabel#emptyTitle { color: #DADDE1; font-size: 16px; font-weight: 650; }
            QLabel#emptyDescription { color: #737B84; font-size: 11px; }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 2px;
            }
            QScrollBar::handle:vertical {
                background: #343A41;
                min-height: 34px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover { background: #4A525B; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical,
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { height: 0; background: none; }
            QStatusBar {
                background: #0E1013;
                color: #737B84;
                border-top: 1px solid #23272C;
                font-size: 10px;
            }
            QToolTip {
                background: #20242A;
                color: #ECEEF0;
                border: 1px solid #3A4149;
                padding: 6px;
            }

            /* Playlist dialog */
            QDialog#playlistDialog { background: #0B0D0F; color: #E8EAED; }
            QFrame#dialogHeader {
                background: #111418;
                border: 1px solid #262B31;
                border-radius: 12px;
            }
            QLabel#dialogEyebrow {
                color: #7F8993;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 1.4px;
            }
            QLabel#dialogTitle { color: #F1F2F4; font-size: 20px; font-weight: 700; }
            QPushButton#dialogHeart {
                background: #15191E;
                color: #C9CED4;
                border: 1px solid #2A2F36;
                border-radius: 9px;
                padding: 6px 12px;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#dialogHeart:hover { border-color: #FF6B81; color: #FF6B81; }
            QPushButton#dialogHeart[on="true"] {
                background: #1F1113; color: #FF4D67; border-color: #FF4D67;
            }
            QLabel#dialogSubtitle, QLabel#selectionInfo, QLabel#pathLabel,
            QLabel#downloadLog { color: #7C848D; font-size: 10px; }
            QLabel#listHeading { color: #BFC4CA; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
            QListWidget#episodeList {
                background: #101317;
                color: #DADDE1;
                border: 1px solid #282D33;
                border-radius: 10px;
                padding: 6px;
                font-size: 11px;
            }
            QListWidget#episodeList::item {
                min-height: 38px;
                padding: 0 10px;
                border-radius: 7px;
                border-bottom: 1px solid #1C2025;
            }
            QListWidget#episodeList::item:hover { background: #1A1E23; }
            QListWidget#episodeList::item:selected {
                background: #2A333C;
                color: #F1F3F5;
            }
            QListWidget#episodeList::indicator {
                width: 18px; height: 18px;
                border: 1px solid #4A515A;
                border-radius: 4px;
                background: #0C0F12;
                margin-right: 4px;
            }
            QListWidget#episodeList::indicator:hover { border-color: #7D98B0; }
            QListWidget#episodeList::indicator:checked {
                background: #E0623F;
                border-color: #E0623F;
            }
            QLineEdit#rangeInput {
                background: #0C0F12;
                color: #DADDE1;
                border: 1px solid #282D33;
                border-radius: 8px;
                padding: 6px 10px;
                font-size: 11px;
            }
            QLineEdit#rangeInput:focus { border-color: #7D98B0; }
            QFrame#downloadPanel {
                background: #111418;
                border: 1px solid #272C32;
                border-radius: 11px;
            }
            QProgressBar#downloadProgress {
                background: #0B0D10;
                color: #D9DDE1;
                border: 1px solid #292E34;
                border-radius: 6px;
                height: 18px;
                text-align: center;
                font-size: 9px;
            }
            QProgressBar#downloadProgress::chunk { background: #7D98B0; border-radius: 5px; }
            QFrame#tabBar { background: transparent; }
            QFrame#discoverTab {
                background: #111418;
                border: 1px solid #262B31;
                border-radius: 10px;
            }
            QFrame#discoverTab:hover { border: 1px solid #3A4048; }
            QFrame#discoverTab[selected="true"] {
                background: #1C130F;
                border: 1px solid #E0623F;
            }
            QLabel#tabTitle { color: #E8EAED; font-size: 13px; font-weight: 700; }
            QLabel#tabSubtitle { color: #7D858F; font-size: 9px; }
            QFrame#discoverTab[selected="true"] QLabel#tabTitle { color: #F2A488; }
            """
        )


class PlaylistDialog(QDialog):
    def __init__(self, parent, src, cid, name, result):
        super().__init__(parent)
        self.setObjectName("playlistDialog")
        self.setWindowTitle(f"Playlist — {name}")
        self.resize(590, 720)
        self.setMinimumSize(520, 620)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.src = src
        self.cid = cid
        self.parent_ref = parent

        _, payload = result
        data = payload.get("data")
        self.eps = data if isinstance(data, list) else (data or {}).get("list", [])
        self.eps = self.eps or []

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(14)

        header = QFrame()
        header.setObjectName("dialogHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(18, 15, 18, 15)
        header_layout.setSpacing(3)
        eyebrow = QLabel("BỘ SƯU TẬP")
        eyebrow.setObjectName("dialogEyebrow")
        title = QLabel(name)
        title.setObjectName("dialogTitle")
        title.setWordWrap(True)
        self.title_label = title
        self.subtitle = QLabel(f"{src['name']}  •  {len(self.eps)} tập  •  ID {cid}")
        self.subtitle.setObjectName("dialogSubtitle")
        self.synopsis = QLabel("")
        self.synopsis.setObjectName("dialogSynopsis")
        self.synopsis.setWordWrap(True)
        self.synopsis.setVisible(False)
        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.addWidget(eyebrow)
        top_row.addStretch(1)
        self.fav_btn = QPushButton()
        self.fav_btn.setObjectName("dialogHeart")
        self.fav_btn.setCursor(Qt.PointingHandCursor)
        self.fav_btn.clicked.connect(self._toggle_fav)
        top_row.addWidget(self.fav_btn)
        header_layout.addLayout(top_row)
        header_layout.addWidget(title)
        header_layout.addWidget(self.subtitle)
        header_layout.addWidget(self.synopsis)
        root.addWidget(header)
        self._cover_url = ""
        self._refresh_fav()

        hint = payload.get("_hint") if isinstance(payload, dict) else None
        if not self.eps and hint:
            warn = QLabel("⚠  " + hint)
            warn.setObjectName("dialogWarn")
            warn.setWordWrap(True)
            root.addWidget(warn)

        tools = QHBoxLayout()
        tools.setSpacing(8)
        list_heading = QLabel("DANH SÁCH TẬP")
        list_heading.setObjectName("listHeading")
        select_all = QPushButton("Chọn tất cả")
        select_all.setObjectName("secondaryButton")
        select_all.clicked.connect(self._select_all)
        clear_selection = QPushButton("Bỏ chọn")
        clear_selection.setObjectName("secondaryButton")
        clear_selection.clicked.connect(self._clear_selection)
        tools.addWidget(list_heading)
        tools.addStretch(1)
        tools.addWidget(select_all)
        tools.addWidget(clear_selection)
        root.addLayout(tools)

        range_row = QHBoxLayout()
        range_row.setSpacing(8)
        self.range_input = QLineEdit()
        self.range_input.setObjectName("rangeInput")
        self.range_input.setPlaceholderText("Chọn nhanh theo tập, vd: 1-10, 15, 20-30")
        self.range_input.setClearButtonEnabled(True)
        self.range_input.returnPressed.connect(self._apply_range)
        range_btn = QPushButton("Chọn dải")
        range_btn.setObjectName("secondaryButton")
        range_btn.setCursor(Qt.PointingHandCursor)
        range_btn.clicked.connect(self._apply_range)
        range_row.addWidget(self.range_input, 1)
        range_row.addWidget(range_btn)
        root.addLayout(range_row)

        self.lst = QListWidget()
        self.lst.setObjectName("episodeList")
        self.lst.setSelectionMode(QListWidget.NoSelection)
        for episode in self.eps:
            is_free = episode.get("is_free") in ("T", True, 1)
            access = "FREE" if is_free else "VIP "
            sequence = episode.get("sequence_no", "?")
            episode_id = episode.get("id")
            item = QListWidgetItem(f"  {access}     Tập {sequence}     •     ID {episode_id}")
            item.setSizeHint(QSize(0, 42))
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.lst.addItem(item)
        self.lst.itemChanged.connect(self._update_selection)
        self.lst.viewport().installEventFilter(self)
        root.addWidget(self.lst, 1)

        self.selection_info = QLabel()
        self.selection_info.setObjectName("selectionInfo")
        root.addWidget(self.selection_info)

        panel = QFrame()
        panel.setObjectName("downloadPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(14, 12, 14, 12)
        panel_layout.setSpacing(10)
        destination_row = QHBoxLayout()
        destination_row.setSpacing(8)
        self.dir = QLabel(parent.outdir)
        self.dir.setObjectName("pathLabel")
        self.dir.setToolTip(parent.outdir)
        self.dir.setTextInteractionFlags(Qt.TextSelectableByMouse)
        pick = QPushButton("Đổi thư mục")
        pick.setObjectName("secondaryButton")
        pick.clicked.connect(self.pick_dir)
        destination_row.addWidget(self.dir, 1)
        destination_row.addWidget(pick)
        panel_layout.addLayout(destination_row)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        self.download_button = QPushButton("Tải các tập đã chọn")
        self.download_button.setObjectName("primaryButton")
        self.download_button.setCursor(Qt.PointingHandCursor)
        self.download_button.clicked.connect(self.download)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.setObjectName("secondaryButton")
        self.stop_button.setCursor(Qt.PointingHandCursor)
        self.stop_button.setVisible(False)
        self.stop_button.clicked.connect(self._stop_download)
        btn_row.addWidget(self.download_button, 1)
        btn_row.addWidget(self.stop_button)
        panel_layout.addLayout(btn_row)

        self.bar = QProgressBar()
        self.bar.setObjectName("downloadProgress")
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setTextVisible(True)
        self.bar.setVisible(False)
        panel_layout.addWidget(self.bar)

        self.log = QLabel("")
        self.log.setObjectName("downloadLog")
        self.log.setWordWrap(True)
        panel_layout.addWidget(self.log)
        root.addWidget(panel)

        self.prog = Sig()
        self._plast = 0.0
        self._stopped = False
        self.finished.connect(lambda *_: setattr(self, "_stopped", True))
        self.prog.ok.connect(self._on_prog)
        self._update_selection()

    def _favs(self):
        return getattr(self.parent_ref, "favs", None)

    def _refresh_fav(self):
        favs = self._favs()
        if favs is None:
            self.fav_btn.setVisible(False)
            return
        on = favs.is_fav(self.src.get("app_id"), self.cid, self.src.get("project_id"))
        self.fav_btn.setText("♥  Đã lưu" if on else "♡  Lưu bộ")
        self.fav_btn.setProperty("on", on)
        refresh_style(self.fav_btn)

    def _toggle_fav(self):
        favs = self._favs()
        if favs is None:
            return
        entry = Favorites.entry_from_src(self.src, self.cid, self.title_label.text(),
                                         cover_url=self._cover_url, eps=len(self.eps))
        favs.toggle(entry)
        self._refresh_fav()
        if hasattr(self.parent_ref, "_on_fav_changed"):
            self.parent_ref._on_fav_changed()

    def apply_detail(self, info):
        if not info:
            return
        self._cover_url = info.get("cover_url") or self._cover_url
        favs = self._favs()
        if favs is not None and favs.is_fav(self.src.get("app_id"), self.cid, self.src.get("project_id")):
            favs.add(Favorites.entry_from_src(self.src, self.cid, self.title_label.text(),
                     cover_url=self._cover_url, eps=info.get("total_eps") or len(self.eps)))
            if hasattr(self.parent_ref, "_refresh_fav_button"):
                self.parent_ref._refresh_fav_button()
        bits = []
        total = info.get("total_eps") or 0
        if total and total != len(self.eps):
            bits.append(f"tổng {total} tập")
        if info.get("status"):
            bits.append(info["status"])
        if info.get("author"):
            bits.append("✍ " + info["author"])
        if info.get("tags"):
            bits.append(" / ".join(info["tags"][:3]))
        if info.get("watch_num"):
            bits.append(f"{int(info['watch_num']):,}".replace(",", ".") + " lượt xem")
        head = f"{self.src['name']}  •  {len(self.eps)} tập tải được  •  ID {self.cid}"
        if bits:
            head += chr(10) + "  •  ".join(bits)
        self.subtitle.setText(head)
        if info.get("describe"):
            self.synopsis.setText(info["describe"])
            self.synopsis.setVisible(True)

    def eventFilter(self, obj, event):
        if obj is self.lst.viewport() and event.type() == QEvent.MouseButtonRelease \
                and event.button() == Qt.LeftButton:
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            item = self.lst.itemAt(pos)
            if item is not None and (item.flags() & Qt.ItemIsUserCheckable):
                item.setCheckState(
                    Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
                return True
        return super().eventFilter(obj, event)

    def _set_all_checked(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        self.lst.blockSignals(True)
        for i in range(self.lst.count()):
            self.lst.item(i).setCheckState(state)
        self.lst.blockSignals(False)
        self._update_selection()

    def _select_all(self):
        self._set_all_checked(True)

    def _clear_selection(self):
        self._set_all_checked(False)

    def _apply_range(self):
        spec = self.range_input.text().strip()
        if not spec:
            return
        want = parse_ranges(spec, len(self.eps))
        self.lst.blockSignals(True)
        for i in range(self.lst.count()):
            self.lst.item(i).setCheckState(Qt.Checked if (i + 1) in want else Qt.Unchecked)
        self.lst.blockSignals(False)
        self._update_selection()

    def _checked_rows(self):
        return [i for i in range(self.lst.count())
                if self.lst.item(i).checkState() == Qt.Checked]

    def _update_selection(self, *args):
        count = len(self._checked_rows())
        if count:
            self.selection_info.setText(f"Đã chọn {count}/{len(self.eps)} tập")
            self.download_button.setText(f"Tải {count} tập đã chọn")
        else:
            self.selection_info.setText("Chưa chọn tập nào • mặc định sẽ tải toàn bộ")
            self.download_button.setText(f"Tải toàn bộ {len(self.eps)} tập")

    def _on_prog(self, payload):
        fraction, text = payload
        self.bar.setVisible(True)
        self.bar.setValue(int(max(0.0, min(1.0, fraction)) * 100))
        self.log.setText(text)

    def _stop_download(self):
        self._stopped = True
        self.stop_button.setEnabled(False)
        self.stop_button.setText("Đang dừng…")
        self.log.setText("Đang dừng sau tập hiện tại…")

    def pick_dir(self):
        directory = QFileDialog.getExistingDirectory(
            self,
            "Chọn thư mục lưu",
            self.parent_ref.outdir,
        )
        if directory:
            self.parent_ref.outdir = directory
            self.dir.setText(directory)
            self.dir.setToolTip(directory)

    def download(self):
        rows = self._checked_rows()
        rows = rows or list(range(len(self.eps)))
        total_selected = len(rows)
        if not total_selected:
            self.log.setText("Playlist chưa có tập để tải.")
            return

        self._stopped = False
        self.download_button.setEnabled(False)
        self.stop_button.setVisible(True)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("Dừng")
        self.bar.setVisible(True)
        self.bar.setValue(0)
        self.log.setText(f"Đang chuẩn bị tải {total_selected} tập…")
        cli = self.parent_ref.cli
        episodes = self.eps
        outdir = self.parent_ref.outdir
        source = self.src
        dialog = self
        stop = lambda: dialog._stopped

        def emit(fraction, text):
            now = time.time()
            if now - dialog._plast > 0.12 or fraction >= 1.0:
                dialog._plast = now
                try:
                    dialog.prog.ok.emit((fraction, text))
                except RuntimeError:
                    pass

        def work():
            os.makedirs(outdir, exist_ok=True)
            succeeded = 0
            stopped = False
            for position, row in enumerate(rows, 1):
                if stop():
                    stopped = True
                    break
                episode = episodes[row]
                sequence = episode.get("sequence_no", "?")
                access = "FREE" if episode.get("is_free") in ("T", True, 1) else "VIP"

                def callback(done, total, speed, phase, pos=position, seq=sequence, level=access):
                    fraction = (done / total) if total else 0.0
                    downloaded_mb = done // 1024 // 1024
                    total_mb = total // 1024 // 1024 if total else "?"
                    emit(
                        fraction,
                        f"[{pos}/{total_selected}] Tập {seq} • {level} • {phase} • "
                        f"{fraction * 100:.0f}% • {downloaded_mb}/{total_mb} MB • {speed:.0f} KB/s",
                    )

                if download_episode(cli, source, episode, outdir, log=print, progress=callback, stop=stop):
                    succeeded += 1
                if stop():
                    stopped = True
                    break
            if stopped:
                return f"Đã dừng • tải xong {succeeded}/{total_selected} tập • {outdir}"
            return f"Hoàn tất {succeeded}/{total_selected} tập • {outdir}"

        def _reset_buttons():
            dialog.download_button.setEnabled(True)
            dialog.stop_button.setVisible(False)
            dialog.stop_button.setEnabled(True)
            dialog.stop_button.setText("Dừng")

        def completed(message):
            if not message.startswith("Đã dừng"):
                dialog.bar.setValue(100)
            dialog.log.setText(message)
            _reset_buttons()
            p = dialog.parent_ref
            if hasattr(p, "note_downloaded"):
                p.note_downloaded(dialog.src, dialog.cid, dialog.title_label.text(),
                                  cover_url=getattr(dialog, "_cover_url", ""), eps=len(dialog.eps))
            dialog._refresh_fav()

        def failed(message):
            dialog.log.setText("Không thể tải: " + message)
            _reset_buttons()

        run(work, ok=completed, err=failed)


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 10))
    if os.path.exists(APP_LOGO_PATH):
        app.setWindowIcon(QIcon(APP_LOGO_PATH))
    window = Main()
    window.show()

    if os.environ.get("GUI_SMOKETEST"):
        shot = os.environ.get("GUI_SHOT", os.path.join(HERE, "gui_shot.png"))
        QTimer.singleShot(
            2500,
            lambda: (window.grab().save(shot), print("shot ->", shot), app.quit()),
        )
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
