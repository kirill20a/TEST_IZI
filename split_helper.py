#!/usr/bin/env python3
"""Простой вариант: слева тест, справа чат DeepSeek в браузере.

Программа фотографирует тест, отправляет снимок в чат, ждёт ответ,
читает его со страницы и сама отмечает варианты слева.

Горячие клавиши:
  F6  - навести мышь на кнопку «Копировать» под последним ответом DeepSeek
        (режим copy_button) или на пустое место в переписке (режим page) и нажать
  F7  - навести мышь на поле ввода чата и нажать
  F8  - решить один вопрос
  F9  - включить/выключить нажатие кнопки «Дальше»
  F10 - автопилот до конца теста
  F12 - выход (срабатывает сразу, даже посреди ответа)
"""
from __future__ import annotations

import io
import json
import os
import queue
import random
import re
import sys
import threading
import time
from pathlib import Path

from PIL import Image, ImageDraw

# В собранном .exe настройки и скриншоты лежат рядом с самим .exe
if getattr(sys, "frozen", False):
    HERE = Path(sys.executable).resolve().parent
else:
    HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "split_config.json"

END_MARK = "Конец задания."
READY = "ГОТОВ"
# Инструкция отправляется один раз в начале чата
PROMPT = (
    "Я буду присылать скриншоты вопросов теста по одному, у каждого вопроса есть номер.\n"
    "Пронумеруй варианты ответа сверху вниз, начиная с 1, и ответь ОДНОЙ строкой, "
    "без пояснений, рассуждений и оформления: слово ОТВЕТ, номер вопроса, двоеточие "
    "и номера правильных вариантов через запятую. Например, для вопроса номер N: "
    "ОТВЕТ N: 2 или ОТВЕТ N: 1, 3\n"
    "Если в вопросе нужно ввести ответ текстом (поле со свободным вводом), напиши после "
    "двоеточия сам ответ, например ОТВЕТ N: SELECT\n"
    "Если ты не уверен в ответе хотя бы на 75%, ответь ОТВЕТ N: ?\n"
    "Сейчас ответь одним словом: " + READY + "\n"
    + END_MARK
)
# Строка к каждому скриншоту; {tag} - номер вопроса
QUESTION_TEXT = "Вопрос {tag}. Ответ в формате ОТВЕТ {tag}: номера вариантов"
# Для соответствия: первый список уже открыт на скриншоте
QUESTION_TEXT_MATCH = ("Вопрос {tag}. Это задание на соответствие: слева {rows} строк, у каждой "
                       "справа выпадающий список, первый список открыт на скриншоте. Пронумеруй "
                       "варианты в открытом списке сверху вниз, начиная с 1, прочерк не считай. "
                       "Напиши ОТВЕТ {tag}, двоеточие и для каждой строки сверху вниз номер "
                       "подходящего варианта через запятую, всего {rows} номеров")
# Для порядка: номера блоков видны на скриншоте слева
QUESTION_TEXT_ORDER = ("Вопрос {tag}. Это задание на порядок: {rows} блоков, у каждого слева "
                       "номер. Напиши ОТВЕТ {tag}, двоеточие и номера блоков в правильном порядке "
                       "сверху вниз через запятую, всего {rows} номеров")
# Для поля ввода; после номера нет двоеточия, чтобы программа не приняла эту строку за ответ
QUESTION_TEXT_INPUT = ("Вопрос {tag}. Здесь ответ вводится текстом. Напиши ОТВЕТ {tag}, "
                       "двоеточие и только сам ответ, без кавычек и пояснений")

DEFAULT_CONFIG = {
    "monitor": 1,
    "test_side": "left",     # left | right - где открыт тест
    "chat_input": None,      # [x, y] поле ввода, задаётся F7
    "chat_area": None,       # [x, y] кнопка «Копировать» или место в переписке, задаётся F6
    "read_mode": "copy_button",  # copy_button - клик по кнопке; page - Ctrl+A по переписке
    "chat_mode": "screen",   # screen - чат рядом на экране; browser - чат в фоновом окне браузера
    "chat_url": "https://chat.deepseek.com/",
    "debug_port": 9222,
    "browser_path": "",          # пусто - найти Chrome или Edge самому
    "chat_input_selector": "",   # пусто - найти поле ввода самому
    "chat_file_selector": "",    # пусто - найти загрузку файлов самому
    "upload_wait": 4.0,      # ожидание загрузки картинки перед отправкой
    "first_check": 7.0,
    "prompt": PROMPT,        # инструкция, отправляется один раз
    "question_text": QUESTION_TEXT,
    "question_text_input": QUESTION_TEXT_INPUT,
    "question_text_match": QUESTION_TEXT_MATCH,
    "dropdown_wait": 0.6,    # пауза, пока открывается выпадающий список
    "question_text_order": QUESTION_TEXT_ORDER,
    "drag_duration": 0.6,    # за сколько секунд перетаскивать блок
    "press_enter": True,
    "answer_timeout": 20.0,  # сколько максимум ждать ответ
    "auto_next": False,
    "delay_after_answer": 0.6,
    "delay_after_next": 1.5,
    
}

# «ОТВЕТ 482: 1, 3» - номер вопроса, затем номера вариантов
ANSWER_RE = re.compile(r"ОТВЕТ[\s*_№]*(\d{3})[\s*_:.\-–]*(\?|\d+(?:\s*[,;и]\s*\d+)*)")


class StopError(Exception):
    """Причина, по которой программа не кликает и отдаёт вопрос тебе."""


# ---------- конфиг ----------

def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path.exists():
        cfg.update(json.loads(path.read_text(encoding="utf-8")))
    cfg.pop("PROMPT", None)
    if "свободным вводом" not in cfg["prompt"] or END_MARK not in cfg["prompt"]:
        cfg["prompt"] = PROMPT  # старый запрос заменяем новой инструкцией
    if "{tag}" not in cfg.get("question_text", ""):
        cfg["question_text"] = QUESTION_TEXT
    if "{tag}" not in cfg.get("question_text_input", ""):
        cfg["question_text_input"] = QUESTION_TEXT_INPUT
    if "{rows}" not in cfg.get("question_text_match", ""):
        cfg["question_text_match"] = QUESTION_TEXT_MATCH
    if "{rows}" not in cfg.get("question_text_order", ""):
        cfg["question_text_order"] = QUESTION_TEXT_ORDER
    if cfg["answer_timeout"] < 30:
        cfg["answer_timeout"] = 60.0
    save_config(cfg, path)
    return cfg


def save_config(cfg: dict, path: Path = CONFIG_PATH) -> None:
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- чтение ответа ----------

def find_answer(text: str, tag: str) -> list[int] | None:
    """Ответ на вопрос с номером tag. None - такого ответа ещё нет."""
    found = [nums for t, nums in ANSWER_RE.findall(text) if t == tag]
    if not found:
        return None
    if found[-1] == "?":
        return []
    return [int(n) for n in re.findall(r"\d+", found[-1])]


def find_text_answer(text: str, tag: str) -> str | None:
    """Текстовый ответ на вопрос tag. "" - DeepSeek не уверен, None - ответа ещё нет."""
    found = re.findall(rf"ОТВЕТ[\s*_№]*{tag}[\s*_]*[:\-–][ \t*_]*([^\r\n]*)", text)
    found = [f for f in found if f.strip(" *_`")]
    if not found:
        return None
    value = found[-1].strip().strip("*_`").strip().strip("«»\"'").strip()
    return "" if value == "?" else value


def has_instruction(page_text: str) -> bool:
    return END_MARK in page_text


def is_ready(text: str, copied_only: bool) -> bool:
    if copied_only:  # скопирован один ответ
        return READY in text and END_MARK not in text
    pos = text.rfind(END_MARK)
    return pos >= 0 and READY in text[pos + len(END_MARK):]


# ---------- поиск вариантов и кнопки на снимке ----------

def components(mask: bytearray, width: int, height: int, min_size: int = 1):
    """Связные области пикселей маски: (x1, y1, x2, y2, число пикселей)."""
    seen = bytearray(len(mask))
    found = []
    for start in range(len(mask)):
        if not mask[start] or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        x1 = x2 = start % width
        y1 = y2 = start // width
        count = 0
        while stack:
            p = stack.pop()
            count += 1
            x, y = p % width, p // width
            if x < x1: x1 = x
            if x > x2: x2 = x
            if y < y1: y1 = y
            if y > y2: y2 = y
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= height:
                    continue
                row = ny * width
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if 0 <= nx < width:
                        q = row + nx
                        if mask[q] and not seen[q]:
                            seen[q] = 1
                            stack.append(q)
        if count >= min_size:
            found.append((x1, y1, x2, y2, count))
    return found


def find_options(img: Image.Image) -> list[tuple[int, int]]:
    """Центры кружков и квадратиков вариантов ответа, сверху вниз."""
    gray = img.convert("L")
    width, height = gray.size
    data = gray.tobytes()
    mask = bytearray(1 if v < 215 else 0 for v in data)
    shapes = []
    for x1, y1, x2, y2, count in components(mask, width, height, 20):
        w, h = x2 - x1 + 1, y2 - y1 + 1
        if not (13 <= w <= 36 and 13 <= h <= 36 and abs(w - h) <= 3):
            continue
        if not 0.12 <= count / (w * h) <= 0.6:
            continue
        # середина должна быть светлой: это пустой кружок или квадратик
        cx1, cx2 = x1 + w // 3, x2 - w // 3
        cy1, cy2 = y1 + h // 3, y2 - h // 3
        inner = [mask[y * width + x] for y in range(cy1, cy2 + 1) for x in range(cx1, cx2 + 1)]
        if inner and sum(inner) / len(inner) > 0.1:
            continue
        shapes.append(((x1 + x2) // 2, (y1 + y2) // 2, w))

    # варианты стоят в одну колонку и одного размера
    best: list = []
    for cx, _, size in shapes:
        column = [s for s in shapes if abs(s[0] - cx) <= 6 and abs(s[2] - size) <= 3]
        if len(column) > len(best):
            best = column
    if len(best) < 2:
        return []
    best.sort(key=lambda s: s[1])
    return [(x, y) for x, y, _ in best]


def find_boxes(img: Image.Image) -> list[tuple[int, int, int, int]]:
    """Поля ввода и выпадающие списки: широкие светлые прямоугольники с тонкой рамкой."""
    gray = img.convert("L")
    width, height = gray.size
    mask = bytearray(1 if v < 232 else 0 for v in gray.tobytes())
    boxes = []
    for x1, y1, x2, y2, count in components(mask, width, height, 100):
        w, h = x2 - x1 + 1, y2 - y1 + 1
        if w < 150 or not 24 <= h <= 70 or w < 4 * h:
            continue
        if count / (w * h) > 0.2:  # залитый прямоугольник - это кнопка, а не поле
            continue
        # рамка должна быть со всех сторон
        top = sum(mask[y1 * width + x] for x in range(x1, x2 + 1)) / w
        bottom = sum(mask[y2 * width + x] for x in range(x1, x2 + 1)) / w
        if top < 0.8 or bottom < 0.8:
            continue
        boxes.append((x1, y1, x2, y2))
    return sorted(boxes, key=lambda b: b[1])


def find_text_input(img: Image.Image) -> tuple[int, int, int] | None:
    """Одно поле для ввода ответа: (x центра, y центра, нижний край)."""
    boxes = find_boxes(img)
    # несколько одинаковых полей друг под другом - это списки соответствия, не ввод
    if len(boxes) != 1:
        return None
    x1, y1, x2, y2 = boxes[0]
    return (x1 + x2) // 2, (y1 + y2) // 2, y2


def find_sort_rows(img: Image.Image) -> list[tuple[int, int]]:
    """Серые ручки блоков для перетаскивания (↕ 1., ↕ 2., ...) в одну колонку, сверху вниз."""
    gray = img.convert("L")
    width, height = gray.size
    mask = bytearray(1 if 205 <= v <= 233 else 0 for v in gray.tobytes())
    handles = []
    for x1, y1, x2, y2, count in components(mask, width, height, 300):
        w, h = x2 - x1 + 1, y2 - y1 + 1
        if not (35 <= w <= 110 and 28 <= h <= 70) or count / (w * h) < 0.6:
            continue
        handles.append((x1, y1, x2, y2))
    best: list = []
    for b in handles:
        column = [c for c in handles if abs(c[0] - b[0]) <= 4 and abs((c[2] - c[0]) - (b[2] - b[0])) <= 4]
        if len(column) > len(best):
            best = column
    if len(best) < 2:
        return []
    best.sort(key=lambda b: b[1])
    return [((x1 + x2) // 2, (y1 + y2) // 2) for x1, y1, x2, y2 in best]


def find_selects(img: Image.Image) -> list[tuple[int, int, int]]:
    """Выпадающие списки соответствия в одну колонку: [(x, y, нижний край)], сверху вниз."""
    boxes = find_boxes(img)
    best: list = []
    for b in boxes:
        column = [c for c in boxes if abs(c[0] - b[0]) <= 6 and abs((c[2] - c[0]) - (b[2] - b[0])) <= 10]
        if len(column) > len(best):
            best = column
    if len(best) < 2:
        return []
    return [((x1 + x2) // 2, (y1 + y2) // 2, y2) for x1, y1, x2, y2 in best]


def find_next_button(img: Image.Image, below_y: int) -> tuple[int, int] | None:
    """Цветная залитая кнопка ниже последнего варианта."""
    rgb = img.convert("RGB")
    width, height = rgb.size
    raw = rgb.tobytes()
    mask = bytearray(len(raw) // 3)
    for i in range(len(mask)):
        r, g, b = raw[3 * i], raw[3 * i + 1], raw[3 * i + 2]
        hi, lo = max(r, g, b), min(r, g, b)
        mask[i] = 1 if hi - lo > 60 and hi > 80 else 0
    best = None
    for x1, y1, x2, y2, count in components(mask, width, height, 1500):
        w, h = x2 - x1 + 1, y2 - y1 + 1
        if w < 60 or h < 20 or h > 120 or count / (w * h) < 0.55 or y1 <= below_y:
            continue
        if best is None or y1 < best[1]:
            best = (x1, y1, x2, y2)
    if best is None:
        return None
    return (best[0] + best[2]) // 2, (best[1] + best[3]) // 2


# ---------- система ----------

def image_to_dib(img: Image.Image) -> bytes:
    buffer = io.BytesIO()
    img.convert("RGB").save(buffer, "BMP")
    return buffer.getvalue()[14:]


def test_area(monitor: dict, side: str) -> dict:
    if side == "full":  # тест на весь экран, ИИ в фоне
        return {k: monitor[k] for k in ("left", "top", "width", "height")}
    half = monitor["width"] // 2
    left = monitor["left"] + (half if side == "right" else 0)
    return {"left": left, "top": monitor["top"], "width": half, "height": monitor["height"]}


class Clipboard:
    def __init__(self):
        import win32clipboard
        self.wc = win32clipboard
        # Буфер открываем без своего окна: окно без обработки сообщений
        # подвешивает копирование в Chrome (он ждёт ответа от владельца буфера).

    def _open(self):
        for _ in range(40):
            try:
                self.wc.OpenClipboard()
                return
            except Exception:
                time.sleep(0.05)
        raise StopError("буфер обмена занят")

    def _put(self, items: list[tuple[int, object]]) -> None:
        """Кладёт данные в буфер. Если Windows отказывает (буфер в этот момент
        занят, например Chrome ещё дописывает скопированное), пробует снова."""
        last = None
        for _ in range(15):
            try:
                self._open()
            except StopError as exc:
                last = exc
                continue
            try:
                self.wc.EmptyClipboard()
                for fmt, data in items:
                    self.wc.SetClipboardData(fmt, data)
                return
            except Exception as exc:  # например, (6, 'SetClipboardData', 'Неверный дескриптор.')
                last = exc
            finally:
                try:
                    self.wc.CloseClipboard()
                except Exception:
                    pass
            time.sleep(0.1)
        raise StopError(f"не удалось записать в буфер обмена: {last}")

    def text(self, value: str) -> None:
        self._put([(self.wc.CF_UNICODETEXT, value)])

    def image(self, img: Image.Image) -> None:
        # кладём картинку сразу в двух форматах: PNG (его берёт Chrome) и DIB
        png = io.BytesIO()
        img.convert("RGB").save(png, "PNG")
        self._put([(self.wc.RegisterClipboardFormat("PNG"), png.getvalue()),
                   (self.wc.CF_DIB, image_to_dib(img))])

    def read(self) -> str:
        self._open()
        try:
            if self.wc.IsClipboardFormatAvailable(self.wc.CF_UNICODETEXT):
                return self.wc.GetClipboardData(self.wc.CF_UNICODETEXT)
            return ""
        finally:
            self.wc.CloseClipboard()


class Screen:
    def __init__(self):
        if sys.platform == "win32":
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError):
                ctypes.windll.user32.SetProcessDPIAware()
        import mss
        self.mss = mss

    def grab(self, monitor_index: int, side: str) -> tuple[Image.Image, dict]:
        with self.mss.mss() as sct:
            area = test_area(sct.monitors[monitor_index], side)
            raw = sct.grab(area)
            return Image.frombytes("RGB", raw.size, raw.rgb), area


def save_marked(img: Image.Image, options, button=None, answer=()) -> None:
    """Сохраняет снимок с найденными вариантами, чтобы проверить, что видит программа."""
    marked = img.convert("RGB")
    draw = ImageDraw.Draw(marked)
    for n, (x, y) in enumerate(options, 1):
        color = (0, 170, 0) if n in answer else (230, 0, 0)
        draw.ellipse((x - 16, y - 16, x + 16, y + 16), outline=color, width=3)
        draw.text((x + 20, y - 8), str(n), fill=color)
    if button:
        x, y = button
        draw.rectangle((x - 40, y - 15, x + 40, y + 15), outline=(0, 90, 255), width=3)
    folder = HERE / "screenshots"
    folder.mkdir(exist_ok=True)
    marked.save(folder / "last.png")


def make_unicode_typer():
    """Печатает текст символами Unicode, не завися от раскладки клавиатуры (только Windows)."""
    import ctypes

    # размеры полей как в 64-битной Windows: WORD 2 байта, DWORD и LONG 4, указатель 8
    WORD, DWORD, LONG, ULONG_PTR = ctypes.c_uint16, ctypes.c_uint32, ctypes.c_int32, ctypes.c_size_t

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD),
                    ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD),
                    ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]

    class _UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", DWORD), ("u", _UNION)]

    INPUT_KEYBOARD, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 1, 0x0002, 0x0004
    send_input = ctypes.windll.user32.SendInput
    send_input.argtypes = (ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int)
    send_input.restype = ctypes.c_uint

    def type_text(text: str) -> None:
        raw = text.encode("utf-16-le")
        for i in range(0, len(raw), 2):
            code = int.from_bytes(raw[i:i + 2], "little")
            events = (INPUT * 2)(
                INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE, 0, 0)),
                INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, 0)),
            )
            send_input(2, events, ctypes.sizeof(INPUT))
            time.sleep(0.01)

    return type_text


def beep() -> None:
    try:
        import winsound
        winsound.MessageBeep()
    except ImportError:
        print("\a", end="", flush=True)


# ---------- основная логика ----------

class Helper:
    def __init__(self, cfg: dict, screen, clipboard, gui, save=save_config, sleep=time.sleep,
                 typer=None, chat=None):
        self.cfg = cfg
        self.chat = chat  # BrowserChat для режима «ИИ в фоне»; создаётся при первом обращении
        self.typer = typer  # печатает текст с клавиатуры, в том числе русские буквы
        self.screen = screen
        self.clipboard = clipboard
        self.gui = gui
        self.save = save
        self.sleep = sleep
        self.autopilot = threading.Event()
        self.instructed = False  # для режима copy_button: инструкция уже отправлена
        # номера вопросов идут подряд (301, 302, ...); сотня своя у каждого запуска,
        # чтобы не совпасть со старыми ответами в том же чате
        self.counter = random.randint(1, 8) * 100

    def next_tag(self) -> str:
        self.counter += 1
        if self.counter > 999:
            self.counter = 101
        return str(self.counter)

    @property
    def browser_mode(self) -> bool:
        return self.cfg.get("chat_mode", "screen") == "browser"

    @property
    def side(self) -> str:
        """Какую часть экрана снимать: в режиме «ИИ в фоне» тест занимает весь экран."""
        return "full" if self.browser_mode else self.cfg["test_side"]

    @property
    def copy_mode(self) -> bool:
        if self.browser_mode:
            return False
        return self.cfg.get("read_mode", "copy_button") == "copy_button"

    def get_chat(self):
        if self.chat is None:
            from browser_chat import BrowserChat
            self.chat = BrowserChat(self.cfg, HERE / "screenshots" / "question.png", sleep=self.sleep)
        return self.chat

    def chat_call(self, method: str, *args):
        """Вызов браузерного чата; его ошибки превращаются в понятную остановку."""
        from browser_chat import ChatError
        try:
            return getattr(self.get_chat(), method)(*args)
        except ChatError as exc:
            raise StopError(str(exc)) from exc

    def read(self) -> str:
        """Текст ответа: через кнопку «Копировать», всей переписки или из фонового браузера."""
        if self.browser_mode:
            return self.chat_call("read")
        if not self.copy_mode:
            return self.read_page()
        self.gui.press("esc")
        self.clipboard.text("")  # кнопки ещё нет - останется пусто
        self.gui.click(*self.cfg["chat_area"])
        self.sleep(0.4)
        return self.clipboard.read()

    def remember(self, key: str, name: str) -> None:
        x, y = self.gui.position()
        self.cfg[key] = [int(x), int(y)]
        self.save(self.cfg)
        print(f"{name} запомнено: {x}, {y}")

    def toggle_next(self) -> None:
        self.cfg["auto_next"] = not self.cfg["auto_next"]
        self.save(self.cfg)
        print(f"Нажатие «Дальше»: {'ВКЛ' if self.cfg['auto_next'] else 'ВЫКЛ'}")

    def read_page(self) -> str:
        """Копирует весь текст переписки. Пустая строка - прочитать не удалось."""
        for _ in range(2):
            self.gui.press("esc")  # закрыть просмотр картинки, если клик на неё попал
            self.clipboard.text("")  # чтобы не прочитать старый текст из буфера
            self.gui.click(*self.cfg["chat_area"])
            self.sleep(0.2)
            self.gui.hotkey("ctrl", "a")
            self.gui.hotkey("ctrl", "c")
            self.sleep(0.2)
            text = self.clipboard.read()
            if text.strip():
                return text
        return ""

    def focus_input(self) -> None:
        """Первый клик активирует окно, второй ставит курсор в поле ввода."""
        self.gui.click(*self.cfg["chat_input"])
        self.sleep(0.3)

    def paste_text(self, message: str) -> None:
        """Вставляет текст и проверяет, что он действительно попал в поле ввода."""
        check = message.strip().splitlines()[-1]
        for attempt in range(3):
            self.focus_input()
            self.clipboard.text(message)
            self.gui.hotkey("ctrl", "v")
            self.sleep(0.4)
            self.gui.hotkey("ctrl", "a")  # в поле ввода выделит только его текст
            self.gui.hotkey("ctrl", "c")
            self.sleep(0.2)
            typed = self.clipboard.read()
            self.gui.hotkey("ctrl", "end")
            if check in typed and len(typed) < 2.5 * len(message):
                return
            if attempt < 2:
                print(f"Текст не попал в поле ввода, пробую ещё раз ({attempt + 2}/3)...")
        raise StopError("не получилось вставить запрос: проверь место поля ввода (F7)")

    def send_instruction(self) -> None:
        """Отправляет инструкцию и ждёт ответ ГОТОВ."""
        print("В чате нет инструкции, отправляю её...")
        if self.browser_mode:
            self.chat_call("send", self.cfg["prompt"], None)
        else:
            self.paste_text(self.cfg["prompt"])
            self.gui.press("enter")
        waited = 0.0
        while waited <= self.cfg["answer_timeout"]:
            self.sleep(2.0)
            waited += 2.5
            if is_ready(self.read(), self.copy_mode):
                print("ИИ получил инструкцию.")
                self.instructed = True
                return
        raise StopError("ИИ не ответил ГОТОВ на инструкцию")

    def send(self, img: Image.Image, tag: str, text_mode: bool = False, rows: int = 0,
             kind: str = "") -> None:
        key = ("question_text_" + kind if kind else
               "question_text_match" if rows else
               "question_text_input" if text_mode else "question_text")
        message = self.cfg[key].replace("{tag}", tag).replace("{rows}", str(rows))
        if self.browser_mode:
            self.chat_call("send", message, img)
            return
        self.paste_text(message)
        self.clipboard.image(img)
        self.gui.hotkey("ctrl", "v")
        self.sleep(self.cfg["upload_wait"])
        if self.cfg.get("press_enter", True):
            self.gui.press("enter")

    def wait_answer(self, tag: str, finder=None):
        """Ждёт ответ с номером вопроса. В режиме page - два одинаковых чтения подряд."""
        self.sleep(self.cfg["first_check"])
        waited = self.cfg["first_check"]
        previous = None
        text = ""
        while waited <= self.cfg["answer_timeout"]:
            text = self.read()
            answer = (finder or find_answer)(text, tag) if text else None
            if answer is not None and (self.copy_mode or answer == previous):
                return answer
            previous = answer
            self.sleep(2.0)
            waited += 2.5
        # сохраняем то, что программа прочитала, чтобы понять причину
        folder = HERE / "screenshots"
        folder.mkdir(exist_ok=True)
        (folder / "last_page.txt").write_text(text, encoding="utf-8")
        tail = " ".join(text.split())[-150:]
        print(f"Последнее, что прочитано со страницы: ...{tail}")
        raise StopError(f"не нашёл ответ вида «ОТВЕТ {tag}: 2» (прочитанное в screenshots/last_page.txt)")

    def solve(self) -> bool:
        """Один вопрос. True - можно продолжать автопилот."""
        cfg = self.cfg
        if not self.browser_mode and (not cfg["chat_input"] or not cfg["chat_area"]):
            raise StopError("сначала задай поле ввода (F7) и место в переписке (F6)")
        back = self.gui.position()
        img, area = self.screen.grab(cfg["monitor"], self.side)
        options = find_options(img)
        selects = [] if options else find_selects(img)
        rows = [] if options or selects else find_sort_rows(img)
        field = None if options or selects or rows else find_text_input(img)
        save_marked(img, options or [s[:2] for s in selects] or rows or ([field[:2]] if field else []))
        if not options and not selects and not rows and not field:
            print("Варианты ответа, списки, блоки и поле ввода не найдены: тест закончился "
                  "или это задание другого типа. Реши его сам.")
            beep()
            return False

        if self.copy_mode:
            if not self.instructed:
                self.send_instruction()
        else:
            page = self.read()
            if not page:
                raise StopError("не получается прочитать текст чата" + (
                    "" if self.browser_mode else ": переставь точку F6 на пустое место"))
            if not has_instruction(page):
                self.send_instruction()
        tag = self.next_tag()
        if selects:
            return self.solve_match(tag, selects, area, back)
        if rows:
            return self.solve_order(tag, img, rows, back)
        if field:
            self.send(img, tag, text_mode=True)
            print(f"Вопрос {tag} отправлен, это ввод текста. Жду ответ...")
            text_answer = self.wait_answer(tag, find_text_answer)
            if not text_answer:
                raise StopError("ИИ не уверен в ответе")
            print(f"Ответ: {text_answer}")
            self.gui.click(area["left"] + field[0], area["top"] + field[1])
            self.sleep(0.2)
            self.gui.hotkey("ctrl", "a")  # стереть, если в поле что-то было
            self.gui.press("backspace")
            if self.typer:  # печатаем ответ, без буфера обмена
                self.typer(text_answer)
            else:
                self.clipboard.text(text_answer)
                self.gui.hotkey("ctrl", "v")
            return self.press_next(back, field[2])

        self.send(img, tag)
        print(f"Вопрос {tag} отправлен, вариантов на экране: {len(options)}. Жду ответ...")
        answer = self.wait_answer(tag)
        if not answer:
            raise StopError("ИИ не уверен в ответе")
        if any(n < 1 or n > len(options) for n in answer) or len(set(answer)) != len(answer):
            raise StopError(f"ответ {answer} не подходит к {len(options)} вариантам")

        print(f"Ответ: {', '.join(map(str, answer))}")
        save_marked(img, options, answer=answer)
        for n in answer:
            x, y = options[n - 1]
            self.gui.click(area["left"] + x, area["top"] + y)
            self.sleep(0.15)
        return self.press_next(back, max(y for _, y in options))

    def open_select(self, area: dict, select) -> None:
        self.gui.click(area["left"] + select[0], area["top"] + select[1])
        self.sleep(self.cfg["dropdown_wait"])

    def solve_match(self, tag: str, selects, area: dict, back) -> bool:
        """Соответствие: открыть первый список, снять экран, выбрать варианты стрелками."""
        cfg = self.cfg
        self.open_select(area, selects[0])
        img, _ = self.screen.grab(cfg["monitor"], self.side)
        self.gui.press("esc")  # закрыть список, ничего не выбирая
        self.sleep(0.2)
        self.send(img, tag, rows=len(selects))
        print(f"Вопрос {tag} отправлен, это соответствие, строк: {len(selects)}. Жду ответ...")
        answer = self.wait_answer(tag)
        if not answer:
            raise StopError("ИИ не уверен в ответе")
        if len(answer) != len(selects) or min(answer) < 1:
            raise StopError(f"ответ {answer} не подходит к {len(selects)} строкам")
        print(f"Ответ: {', '.join(map(str, answer))}")
        for select, number in zip(selects, answer):
            self.open_select(area, select)
            for _ in range(number):  # прочерк стоит первым, вариант N - через N нажатий
                self.gui.press("down")
                self.sleep(0.05)
            self.gui.press("enter")
            self.sleep(0.2)
        return self.press_next(back, selects[-1][2])

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        self.gui.moveTo(*start)
        self.gui.mouseDown()
        self.sleep(0.15)
        self.gui.moveTo(start[0], start[1] - 5, duration=0.1)  # сдвинуть, чтобы сайт понял, что это перенос
        self.gui.moveTo(*end, duration=self.cfg["drag_duration"])
        self.sleep(0.2)
        self.gui.mouseUp()
        self.sleep(0.4)

    def solve_order(self, tag: str, img: Image.Image, rows, back) -> bool:
        """Порядок: перетаскивает блоки по одному на свои места сверху вниз."""
        cfg = self.cfg
        count = len(rows)
        self.send(img, tag, rows=count, kind="order")
        print(f"Вопрос {tag} отправлен, это порядок, блоков: {count}. Жду ответ...")
        answer = self.wait_answer(tag)
        if not answer:
            raise StopError("ИИ не уверен в ответе")
        if sorted(answer) != list(range(1, count + 1)):
            raise StopError(f"ответ {answer} - это не перестановка номеров 1..{count}")
        print(f"Ответ: {', '.join(map(str, answer))}")

        current = list(range(1, count + 1))  # какой исходный блок стоит на каждом месте
        for place, block in enumerate(answer):
            src = current.index(block)
            if src == place:
                continue
            img, area = self.screen.grab(cfg["monitor"], self.side)
            now = find_sort_rows(img)
            if len(now) != count:
                raise StopError("после перетаскивания блоки не нашлись, проверь задание")
            (sx, sy), (tx, ty) = now[src], now[place]
            self.drag((area["left"] + sx, area["top"] + sy), (area["left"] + tx, area["top"] + ty))
            current.insert(place, current.pop(src))
        img, area = self.screen.grab(cfg["monitor"], self.side)
        save_marked(img, find_sort_rows(img))
        return self.press_next(back, max(y for _, y in rows))

    def press_next(self, back, below_y: int) -> bool:
        """Нажимает «Дальше», если это включено. True - можно продолжать автопилот."""
        cfg = self.cfg
        if not cfg["auto_next"]:
            self.gui.moveTo(*back)
            return False
        self.sleep(cfg["delay_after_answer"])
        img, area = self.screen.grab(cfg["monitor"], self.side)
        button = find_next_button(img, below_y)
        if button is None:
            raise StopError("кнопка «Дальше» не найдена")
        self.gui.click(area["left"] + button[0], area["top"] + button[1])
        self.gui.moveTo(*back)
        self.sleep(cfg["delay_after_next"])
        return True

    def run_task(self, task: str) -> None:
        try:
            if task == "solve":
                self.solve()
            elif task == "autopilot":
                if not self.cfg["auto_next"]:
                    print("Сначала включи нажатие «Дальше» (F9).")
                    return
                self.autopilot.set()
                print("Автопилот запущен. Остановить: F10.")
                while self.autopilot.is_set() and self.solve():
                    pass
                if self.autopilot.is_set():
                    print("Автопилот закончил.")
        except StopError as exc:
            print(f"Стоп: {exc}. Ответь на этот вопрос сам.")
            beep()
        finally:
            self.autopilot.clear()


def main() -> None:
    cfg = load_config()
    import pyautogui
    from pynput import keyboard

    pyautogui.FAILSAFE = True  # мышь в угол экрана - аварийная остановка
    # в Windows печатаем символами Unicode: так раскладка клавиатуры не важна
    typer = make_unicode_typer() if sys.platform == "win32" else keyboard.Controller().type
    helper = Helper(cfg, Screen(), Clipboard(), pyautogui, typer=typer)
    tasks: queue.Queue = queue.Queue()
    busy = threading.Event()

    def on_press(key):
        if key == keyboard.Key.f12:
            print("\nВыход.")
            os._exit(0)  # сразу, даже если программа ждёт ответ DeepSeek
        elif key == keyboard.Key.f10 and helper.autopilot.is_set():
            helper.autopilot.clear()
            print("Автопилот остановится после текущего вопроса.")
        elif key == keyboard.Key.f9:
            helper.toggle_next()
        elif not busy.is_set():
            task = {keyboard.Key.f6: "area", keyboard.Key.f7: "input",
                    keyboard.Key.f8: "solve", keyboard.Key.f10: "autopilot"}.get(key)
            if task:
                tasks.put(task)

    listener = keyboard.Listener(on_press=on_press)
    listener.daemon = True
    listener.start()
    print("F6 место в переписке | F7 поле ввода | F8 решить | F9 «Дальше» | F10 автопилот | F12 выход")
    print(f"Нажатие «Дальше»: {'ВКЛ' if cfg['auto_next'] else 'ВЫКЛ'}")

    while True:
        try:
            task = tasks.get()
        except KeyboardInterrupt:
            break
        if task == "quit":
            break
        busy.set()
        try:
            if task == "area":
                helper.remember("chat_area", "Место в переписке")
            elif task == "input":
                helper.remember("chat_input", "Поле ввода")
            else:
                helper.run_task(task)
        except Exception as exc:
            if type(exc).__name__ == "FailSafeException":
                print("Аварийная остановка: мышь в углу экрана.")
            else:
                print(f"Ошибка: {type(exc).__name__}: {exc}")
        finally:
            busy.clear()


if __name__ == "__main__":
    main()