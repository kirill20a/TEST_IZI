"""ИИ-чат в фоновом окне браузера.

Программа подключается к Chrome (или Edge), запущенному с отладочным портом,
и работает с вкладкой чата напрямую: вставляет текст, прикрепляет скриншот
как файл, нажимает Enter и читает текст страницы. Мышь и буфер обмена не нужны,
поэтому окно чата может лежать под тестом.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

# Где искать поле ввода и кнопку загрузки, если в настройках не указано своё
INPUT_SELECTORS = "textarea, [contenteditable='true']"
FILE_SELECTORS = ["input[type=file][accept*='image']", "input[type=file]"]


class ChatError(Exception):
    """Понятная причина, почему с чатом не получилось."""


def find_browser(custom: str = "") -> str | None:
    """Путь к Chrome или Edge в Windows."""
    if custom and Path(custom).exists():
        return custom
    roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    names = [r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"]
    for name in names:
        for root in roots:
            if root and (Path(root) / name).exists():
                return str(Path(root) / name)
    return None


def launch_browser(cfg: dict, profile_dir: Path) -> str:
    """Открывает отдельное окно браузера для ИИ с отладочным портом."""
    exe = find_browser(cfg.get("browser_path", ""))
    if not exe:
        raise ChatError("не нашёл Chrome или Edge; укажи путь к браузеру в настройках (browser_path)")
    profile_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        exe,
        f"--remote-debugging-port={int(cfg['debug_port'])}",
        f"--user-data-dir={profile_dir}",  # отдельный профиль: в обычном порт не откроется
        "--no-first-run",
        "--no-default-browser-check",
        cfg["chat_url"],
    ])
    return exe


class BrowserChat:
    """Работает только из того потока, в котором создан (так устроен playwright)."""

    def __init__(self, cfg: dict, image_path: Path, browser=None, sleep=time.sleep):
        self.cfg = cfg
        self.image_path = image_path
        self.browser = browser
        self.sleep = sleep
        self.page = None
        self._pw = None

    # ----- подключение -----

    def connect(self):
        if self.page is not None and not self.page.is_closed():
            return self.page
        if self.browser is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise ChatError("не установлена библиотека playwright: pip install playwright") from exc
            self._pw = sync_playwright().start()
            port = int(self.cfg["debug_port"])
            try:
                self.browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{port}", timeout=5000)
            except Exception as exc:
                self._pw.stop()
                self._pw = None
                raise ChatError("браузер для ИИ не запущен: нажми «Открыть браузер для ИИ»") from exc
        host = urlparse(self.cfg["chat_url"]).netloc
        pages = [p for ctx in self.browser.contexts for p in ctx.pages]
        url = self.cfg["chat_url"].rstrip("/")
        # сначала вкладка с тем же адресом, потом любая с того же сайта
        found = [p for p in pages if p.url.rstrip("/").startswith(url)] or \
                [p for p in pages if host and host in p.url]
        if found:
            self.page = found[-1]
        else:
            try:
                ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context()
                self.page = ctx.new_page()
                self.page.goto(self.cfg["chat_url"], timeout=30000)
            except Exception as exc:
                self.page = None
                raise ChatError(f"не открылся адрес чата {self.cfg['chat_url']}: проверь адрес и интернет") from exc
        return self.page

    def close(self) -> None:
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
        self._pw = None
        self.browser = None
        self.page = None

    def new_chat(self) -> None:
        page = self.connect()
        try:
            page.goto(self.cfg["chat_url"], timeout=30000)
        except Exception as exc:
            self.page = None
            raise ChatError(f"не открылся адрес чата {self.cfg['chat_url']}") from exc
        page.wait_for_timeout(1500)

    # ----- поиск элементов -----

    def find_input(self):
        page = self.connect()
        selector = self.cfg.get("chat_input_selector") or INPUT_SELECTORS
        boxes = page.locator(selector)
        visible = [boxes.nth(i) for i in range(boxes.count()) if boxes.nth(i).is_visible()]
        if not visible:
            raise ChatError("не нашёл поле ввода на странице чата (проверь, что вход выполнен)")
        return visible[-1]  # поле для сообщения обычно последнее, внизу страницы

    def find_file_input(self):
        page = self.connect()
        custom = self.cfg.get("chat_file_selector")
        for selector in ([custom] if custom else FILE_SELECTORS):
            files = page.locator(selector)
            if files.count():
                return files.first
        raise ChatError("не нашёл на странице чата загрузку файлов, картинку прикрепить нельзя")

    @staticmethod
    def box_text(box) -> str:
        try:
            return box.input_value()
        except Exception:
            return box.inner_text()

    # ----- отправка и чтение -----

    def send(self, text: str, image: Image.Image | None = None) -> None:
        page = self.connect()
        try:
            if image is not None:
                self.image_path.parent.mkdir(parents=True, exist_ok=True)
                image.convert("RGB").save(self.image_path, "PNG")
                self.find_file_input().set_input_files(str(self.image_path))
                page.wait_for_timeout(int(float(self.cfg["upload_wait"]) * 1000))
            box = self.find_input()
            box.fill(text)
            # Enter не сработает, пока картинка ещё грузится: жмём, пока поле не очистится
            for _ in range(int(float(self.cfg["answer_timeout"]))):
                box.press("Enter")
                page.wait_for_timeout(1000)
                if not self.box_text(box).strip():
                    return
            raise ChatError("сообщение не отправилось: поле ввода не очистилось после Enter")
        except ChatError:
            raise
        except Exception as exc:
            self.page = None  # вкладку могли закрыть или перезагрузить - переподключимся
            raise ChatError(f"не получилось отправить в чат: {type(exc).__name__}: {exc}") from exc

    def read(self) -> str:
        page = self.connect()
        try:
            return page.evaluate("document.body.innerText") or ""
        except Exception:
            self.page = None
            return ""
