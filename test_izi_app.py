#!/usr/bin/env python3
"""TEST IZI - окно-приложение для split_helper.

Вся логика решения живёт в split_helper.py, здесь только интерфейс:
кнопки, настройки, журнал и горячие клавиши.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from PIL import Image, ImageDraw

import split_helper as core

APP_NAME = "TEST IZI"

BG = "#f4f5f7"
CARD = "#ffffff"
DARK = "#1f2430"
TEXT = "#1f2430"
MUTED = "#6b7280"
ACCENT = "#2f6fed"
GREEN = "#16a34a"
RED = "#dc2626"
FONT = "Segoe UI"

CHAT_MODES = {"В фоне за тестом": "browser", "Рядом с тестом": "screen"}
READ_MODES = {"Кнопка «Копировать»": "copy_button", "Вся переписка": "page"}
SIDES = {"Слева": "left", "Справа": "right", "Весь экран": "full"}
NUMBERS = [
    ("upload_wait", "Ждать загрузку картинки, с"),
    ("first_check", "Первая проверка ответа через, с"),
    ("answer_timeout", "Максимум ждать ответ, с"),
    ("dropdown_wait", "Ждать открытия списка, с"),
    ("drag_duration", "Длительность перетаскивания, с"),
]
STATES = {
    "idle": ("Готов", "#374151"),
    "solve": ("Решаю вопрос", ACCENT),
    "autopilot": ("Автопилот", GREEN),
    "area": ("Запоминаю место", "#7c3aed"),
    "input": ("Запоминаю место", "#7c3aed"),
    "newchat": ("Открываю новый чат", "#7c3aed"),
}


# ---------- значок ----------

def make_icon(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=ACCENT)
    w = max(2, size // 11)
    draw.line([(size * 0.27, size * 0.52), (size * 0.44, size * 0.69), (size * 0.75, size * 0.33)],
              fill="white", width=w, joint="curve")
    return img


def save_icon(path: str) -> None:
    make_icon(256).save(path, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


# ---------- журнал ----------

class LogWriter:
    """Перехватывает print() из split_helper и отправляет строки в окно."""

    def __init__(self, events: queue.Queue):
        self.events = events
        self.buffer = ""

    def write(self, text: str) -> int:
        self.buffer += text.replace("\a", "")  # символ звукового сигнала журналу не нужен
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                self.events.put(("log", line))
        return len(text)

    def flush(self) -> None:
        pass


# ---------- приложение ----------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = core.load_config()
        self.cfg.setdefault("hide_window", True)
        self.cfg.setdefault("topmost", False)
        self.cfg.setdefault("exit_when_drive_removed", False)
        # файл-метка flash_mode рядом с программой (его создаёт ЗАПУСК.bat на флешке)
        if (core.HERE / "flash_mode").exists():
            self.cfg["exit_when_drive_removed"] = True
        self.events: queue.Queue = queue.Queue()
        self.tasks: queue.Queue = queue.Queue()
        self.busy = threading.Event()
        self.solved = 0
        self.stops = 0
        self.countdown_left = 0

        sys.stdout = sys.stderr = LogWriter(self.events)

        import pyautogui
        from pynput import keyboard
        pyautogui.FAILSAFE = True  # мышь в угол экрана - аварийная остановка
        self.helper = core.Helper(self.cfg, core.Screen(), core.Clipboard(), pyautogui,
                                  typer=core.make_unicode_typer() if sys.platform == "win32"
                                  else keyboard.Controller().type)
        self.keyboard = keyboard

        self.build()
        self.refresh()
        threading.Thread(target=self.worker, daemon=True).start()
        if self.cfg.get("exit_when_drive_removed"):
            threading.Thread(target=self.watch_drive, daemon=True).start()
        listener = keyboard.Listener(on_press=self.on_key)
        listener.daemon = True
        listener.start()
        self.root.after(50, self.poll)
        print("Программа готова. F8 - решить вопрос, F10 - автопилот, F12 - выход.")
        if self.helper.browser_mode:
            print("ИИ в фоне: нажми «Открыть браузер для ИИ» и войди в чат.")
        elif not self.cfg["chat_input"] or not self.cfg["chat_area"]:
            print("Сначала задай поле ввода чата и кнопку «Копировать» в разделе «Чат с ИИ».")

    # ----- интерфейс -----

    def build(self) -> None:
        root = self.root
        root.title(APP_NAME)
        root.configure(bg=BG)
        root.geometry("470x720")
        root.minsize(420, 600)
        root.attributes("-topmost", bool(self.cfg["topmost"]))
        try:
            from PIL import ImageTk
            self.icon = ImageTk.PhotoImage(make_icon(64))
            root.iconphoto(True, self.icon)
        except Exception:
            pass

        style = ttk.Style(root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TCombobox", padding=4)
        style.configure("TCheckbutton", background=CARD, foreground=TEXT, font=(FONT, 10))
        style.configure("TSpinbox", padding=3)

        # шапка
        header = tk.Frame(root, bg=DARK, padx=18, pady=14)
        header.pack(fill="x")
        tk.Label(header, text=APP_NAME, bg=DARK, fg="white", font=(FONT, 17, "bold")).pack(side="left")
        self.status = tk.Label(header, text="", fg="white", font=(FONT, 10, "bold"), padx=12, pady=4)
        self.status.pack(side="right")

        body = tk.Frame(root, bg=BG, padx=14, pady=12)
        body.pack(fill="both", expand=True)

        # управление
        ctrl = self.card(body, "Управление")
        row = tk.Frame(ctrl, bg=CARD)
        row.pack(fill="x")
        self.btn_solve = self.button(row, "Решить вопрос  F8", ACCENT, lambda: self.start("solve"))
        self.btn_solve.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_auto = self.button(row, "Автопилот  F10", GREEN, lambda: self.start("autopilot"))
        self.btn_auto.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.btn_stop = self.button(row, "Стоп", RED, self.stop)
        self.btn_stop.pack(side="left")

        self.next_var = tk.BooleanVar(value=bool(self.cfg["auto_next"]))
        ttk.Checkbutton(ctrl, text="Нажимать «Следующий вопрос» сам  (F9)", variable=self.next_var,
                        command=self.toggle_next).pack(anchor="w", pady=(10, 0))

        stats = tk.Frame(ctrl, bg=CARD)
        stats.pack(fill="x", pady=(8, 0))
        self.stat_label = tk.Label(stats, bg=CARD, fg=MUTED, font=(FONT, 10))
        self.stat_label.pack(side="left")
        tk.Button(stats, text="Новый чат", command=self.new_chat, relief="flat", bg="#eef1f6",
                  fg=TEXT, font=(FONT, 9), padx=8, cursor="hand2").pack(side="right")

        # где ИИ
        setup = self.card(body, "Чат с ИИ")
        self.chat_var = tk.StringVar(value=self.key_for(CHAT_MODES, self.cfg.get("chat_mode", "screen")))
        self.combo_row(setup, "Где ИИ", self.chat_var, list(CHAT_MODES), self.on_chat_mode)

        self.browser_box = tk.Frame(setup, bg=CARD)
        line = tk.Frame(self.browser_box, bg=CARD)
        line.pack(fill="x", pady=3)
        tk.Label(line, text="Адрес чата", bg=CARD, fg=TEXT, font=(FONT, 10)).pack(side="left")
        self.url_var = tk.StringVar(value=self.cfg.get("chat_url", ""))
        ttk.Entry(line, textvariable=self.url_var, width=28).pack(side="right")
        self.button(self.browser_box, "Открыть браузер для ИИ", "#374151", self.open_browser,
                    small=True).pack(anchor="w", pady=(4, 2))
        tk.Label(self.browser_box, text="Войди в чат в открывшемся окне, оставь его под тестом "
                 "(не сворачивай) и разверни тест на весь экран.", bg=CARD, fg=MUTED,
                 font=(FONT, 9), wraplength=400, justify="left").pack(anchor="w")

        self.screen_box = tk.Frame(setup, bg=CARD)
        self.input_label = self.point_row(self.screen_box, "Поле ввода чата  (F7)", "input")
        self.area_label = self.point_row(self.screen_box, "Кнопка «Копировать»  (F6)", "area")
        self.hint = tk.Label(setup, text="", bg=CARD, fg=ACCENT, font=(FONT, 10, "bold"))
        self.hint.pack(anchor="w", side="bottom")
        self.on_chat_mode()

        # настройки
        opts = self.card(body, "Параметры")
        self.mode_var = tk.StringVar(value=self.key_for(READ_MODES, self.cfg.get("read_mode", "copy_button")))
        self.combo_row(opts, "Как читать ответ", self.mode_var, list(READ_MODES))
        self.side_var = tk.StringVar(value=self.key_for(SIDES, self.cfg["test_side"]))
        self.side_row = self.combo_row(opts, "Тест на экране (если ИИ рядом)", self.side_var, list(SIDES))

        self.more_open = False
        self.more_btn = tk.Button(opts, text="Тонкая настройка ▾", command=self.toggle_more, relief="flat",
                                  bg=CARD, fg=ACCENT, font=(FONT, 9), cursor="hand2", bd=0)
        self.more_btn.pack(anchor="w", pady=(4, 0))
        self.more = tk.Frame(opts, bg=CARD)
        self.number_vars = {}
        for key, label in NUMBERS:
            var = tk.StringVar(value=str(self.cfg[key]))
            self.number_vars[key] = var
            line = tk.Frame(self.more, bg=CARD)
            line.pack(fill="x", pady=2)
            tk.Label(line, text=label, bg=CARD, fg=TEXT, font=(FONT, 10)).pack(side="left")
            ttk.Spinbox(line, from_=0, to=300, increment=0.5, textvariable=var, width=6).pack(side="right")
        self.selector_vars = {}
        for key, label in (("chat_input_selector", "Поле ввода (CSS, пусто - само)"),
                           ("chat_file_selector", "Загрузка файла (CSS, пусто - само)")):
            var = tk.StringVar(value=self.cfg.get(key, ""))
            self.selector_vars[key] = var
            line = tk.Frame(self.more, bg=CARD)
            line.pack(fill="x", pady=2)
            tk.Label(line, text=label, bg=CARD, fg=TEXT, font=(FONT, 10)).pack(side="left")
            ttk.Entry(line, textvariable=var, width=16).pack(side="right")
        self.hide_var = tk.BooleanVar(value=bool(self.cfg["hide_window"]))
        ttk.Checkbutton(self.more, text="Прятать окно во время решения", variable=self.hide_var).pack(anchor="w", pady=(6, 0))
        self.top_var = tk.BooleanVar(value=bool(self.cfg["topmost"]))
        ttk.Checkbutton(self.more, text="Поверх всех окон", variable=self.top_var).pack(anchor="w")

        save_row = tk.Frame(opts, bg=CARD)
        save_row.pack(fill="x", pady=(8, 0))
        self.save_row = save_row
        self.button(save_row, "Сохранить", "#374151", self.save_settings, small=True).pack(side="right")

        # журнал
        log_card = self.card(body, "Журнал", expand=True)
        frame = tk.Frame(log_card, bg=CARD)
        frame.pack(fill="both", expand=True)
        self.log = tk.Text(frame, height=8, bg="#fbfbfc", fg=TEXT, font=("Consolas", 9), relief="flat",
                           wrap="word", padx=8, pady=6, state="disabled", highlightthickness=1,
                           highlightbackground="#e5e7eb")
        scroll = ttk.Scrollbar(frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self.log.tag_configure("answer", foreground=GREEN, font=("Consolas", 9, "bold"))
        self.log.tag_configure("stop", foreground=RED, font=("Consolas", 9, "bold"))
        self.log.tag_configure("send", foreground=MUTED)

        tk.Label(root, text="F12 - выход   ·   резко увести мышь в угол экрана - аварийная остановка",
                 bg=BG, fg=MUTED, font=(FONT, 9)).pack(pady=(0, 8))

    def card(self, parent, title: str, expand: bool = False) -> tk.Frame:
        outer = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground="#e5e7eb")
        outer.pack(fill="both" if expand else "x", expand=expand, pady=(0, 10))
        inner = tk.Frame(outer, bg=CARD, padx=14, pady=10)
        inner.pack(fill="both", expand=True)
        tk.Label(inner, text=title.upper(), bg=CARD, fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w", pady=(0, 6))
        return inner

    def button(self, parent, text: str, color: str, command, small: bool = False) -> tk.Button:
        return tk.Button(parent, text=text, command=command, bg=color, fg="white", activebackground=color,
                         activeforeground="white", relief="flat", bd=0, cursor="hand2",
                         font=(FONT, 9 if small else 10, "bold"), padx=12, pady=5 if small else 9,
                         disabledforeground="#e5e7eb")

    def point_row(self, parent, text: str, task: str) -> tk.Label:
        line = tk.Frame(parent, bg=CARD)
        line.pack(fill="x", pady=3)
        tk.Label(line, text=text, bg=CARD, fg=TEXT, font=(FONT, 10)).pack(side="left")
        tk.Button(line, text="Задать", command=lambda: self.countdown(task), relief="flat", bg="#eef1f6",
                  fg=TEXT, font=(FONT, 9), padx=10, cursor="hand2").pack(side="right")
        value = tk.Label(line, bg=CARD, font=(FONT, 9))
        value.pack(side="right", padx=8)
        return value

    def combo_row(self, parent, text: str, var: tk.StringVar, values: list[str], on_change=None):
        line = tk.Frame(parent, bg=CARD)
        line.pack(fill="x", pady=3)
        tk.Label(line, text=text, bg=CARD, fg=TEXT, font=(FONT, 10)).pack(side="left")
        combo = ttk.Combobox(line, textvariable=var, values=values, state="readonly", width=20)
        combo.pack(side="right")
        if on_change:
            combo.bind("<<ComboboxSelected>>", lambda _e: on_change())
        return line

    def on_chat_mode(self) -> None:
        """Показывает настройки для выбранного места чата."""
        browser = CHAT_MODES[self.chat_var.get()] == "browser"
        self.cfg["chat_mode"] = "browser" if browser else "screen"
        if browser:
            self.screen_box.pack_forget()
            self.browser_box.pack(fill="x")
        else:
            self.browser_box.pack_forget()
            self.screen_box.pack(fill="x")
        core.save_config(self.cfg)

    @staticmethod
    def key_for(mapping: dict, value: str) -> str:
        return next((k for k, v in mapping.items() if v == value), next(iter(mapping)))

    def toggle_more(self) -> None:
        self.more_open = not self.more_open
        if self.more_open:
            self.more.pack(fill="x", pady=(4, 0), before=self.save_row)
            self.more_btn.configure(text="Тонкая настройка ▴")
        else:
            self.more.pack_forget()
            self.more_btn.configure(text="Тонкая настройка ▾")

    # ----- состояние -----

    def refresh(self) -> None:
        for label, key in ((self.input_label, "chat_input"), (self.area_label, "chat_area")):
            point = self.cfg.get(key)
            if point:
                label.configure(text=f"✓ {point[0]}, {point[1]}", fg=GREEN)
            else:
                label.configure(text="не задано", fg=RED)
        self.next_var.set(bool(self.cfg["auto_next"]))
        self.stat_label.configure(text=f"Решено: {self.solved}    Остановок: {self.stops}")

    def set_state(self, state: str) -> None:
        text, color = STATES.get(state, STATES["idle"])
        self.status.configure(text=text, bg=color)
        busy = state != "idle"
        for btn in (self.btn_solve, self.btn_auto):
            btn.configure(state="disabled" if busy else "normal")

    def add_log(self, line: str) -> None:
        tag = ""
        if line.startswith("Ответ:"):
            tag = "answer"
            self.solved += 1
        elif line.startswith(("Стоп:", "Ошибка", "Аварийная")):
            tag = "stop"
            self.stops += 1
        elif line.startswith("Вопрос"):
            tag = "send"
        self.log.configure(state="normal")
        self.log.insert("end", line + "\n", tag)
        self.log.see("end")
        self.log.configure(state="disabled")
        self.refresh()

    def poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self.add_log(payload)
                elif kind == "state":
                    self.set_state(payload)
                    self.refresh()
                elif kind == "call":
                    func, done = payload
                    try:
                        func()
                    finally:
                        done.set()
        except queue.Empty:
            pass
        self.root.after(50, self.poll)

    def call_ui(self, func) -> None:
        """Выполнить действие с окном из рабочего потока и дождаться его."""
        done = threading.Event()
        self.events.put(("call", (func, done)))
        done.wait(2)

    # ----- действия -----

    def start(self, task: str) -> None:
        if self.busy.is_set():
            return
        if task == "autopilot" and not self.cfg["auto_next"]:
            print("Для автопилота включи «Нажимать «Следующий вопрос» сам» (F9).")
            return
        self.busy.set()
        self.tasks.put(task)

    def stop(self) -> None:
        if self.helper.autopilot.is_set():
            self.helper.autopilot.clear()
            print("Автопилот остановится после текущего вопроса.")

    def toggle_next(self) -> None:
        if bool(self.next_var.get()) != bool(self.cfg["auto_next"]):
            self.helper.toggle_next()
        self.refresh()

    def new_chat(self) -> None:
        if self.helper.browser_mode:
            self.start("newchat")
            return
        self.helper.instructed = False
        print("Открой в чате новый диалог: инструкция уйдёт туда с первым вопросом.")

    def open_browser(self) -> None:
        from browser_chat import ChatError, launch_browser
        self.cfg["chat_url"] = self.url_var.get().strip() or self.cfg["chat_url"]
        core.save_config(self.cfg)
        try:
            exe = launch_browser(self.cfg, core.HERE / "browser-profile")
        except ChatError as exc:
            print(f"Ошибка: {exc}")
            return
        print(f"Открыл {Path(exe).stem}. Войди в чат, затем положи окно под тест.")

    def countdown(self, task: str) -> None:
        if self.busy.is_set():
            return
        self.countdown_left = 3
        self.countdown_tick(task)

    def countdown_tick(self, task: str) -> None:
        what = "поле ввода DeepSeek" if task == "input" else "кнопку «Копировать»"
        if self.countdown_left > 0:
            self.hint.configure(text=f"Наведи мышь на {what}… {self.countdown_left}")
            self.countdown_left -= 1
            self.root.after(1000, lambda: self.countdown_tick(task))
        else:
            self.hint.configure(text="")
            self.start(task)

    def save_settings(self) -> None:
        try:
            numbers = {key: float(var.get().replace(",", ".")) for key, var in self.number_vars.items()}
        except ValueError:
            print("Не сохранено: в тонкой настройке должны быть числа.")
            return
        self.cfg.update(numbers)
        self.cfg["read_mode"] = READ_MODES[self.mode_var.get()]
        self.cfg["chat_url"] = self.url_var.get().strip() or self.cfg["chat_url"]
        for key, var in self.selector_vars.items():
            self.cfg[key] = var.get().strip()
        self.cfg["test_side"] = SIDES[self.side_var.get()]
        self.cfg["hide_window"] = bool(self.hide_var.get())
        self.cfg["topmost"] = bool(self.top_var.get())
        self.root.attributes("-topmost", self.cfg["topmost"])
        core.save_config(self.cfg)
        print("Настройки сохранены.")

    def on_key(self, key) -> None:
        kb = self.keyboard.Key
        if key == kb.f12:
            os._exit(0)
        elif key == kb.f10 and self.helper.autopilot.is_set():
            self.stop()
        elif key == kb.f9:
            self.helper.toggle_next()
            self.events.put(("call", (self.refresh, threading.Event())))
        elif not self.busy.is_set():
            task = {kb.f6: "area", kb.f7: "input", kb.f8: "solve", kb.f10: "autopilot"}.get(key)
            if task:
                self.events.put(("call", (lambda t=task: self.start(t), threading.Event())))

    # ----- рабочий поток -----

    def watch_drive(self) -> None:
        """Закрывает программу, когда папку (флешку) больше не видно."""
        import time
        folder = str(core.HERE)
        while True:
            time.sleep(1.5)
            if not os.path.isdir(folder):
                os._exit(0)  # флешку вынули - сразу выходим

    def worker(self) -> None:
        while True:
            task = self.tasks.get()
            self.events.put(("state", task))
            hide = task in ("solve", "autopilot") and self.cfg.get("hide_window", True)
            if hide:
                self.call_ui(self.root.withdraw)
                self.helper.sleep(0.3)  # дать окну исчезнуть до скриншота
            try:
                if task == "newchat":
                    self.helper.chat_call("new_chat")
                    self.helper.instructed = False
                    print("Открыт новый чат, инструкция уйдёт с первым вопросом.")
                elif task == "area":
                    self.helper.remember("chat_area", "Кнопка «Копировать»")
                elif task == "input":
                    self.helper.remember("chat_input", "Поле ввода")
                else:
                    self.helper.run_task(task)
            except core.StopError as exc:
                print(f"Стоп: {exc}.")
            except Exception as exc:
                if type(exc).__name__ == "FailSafeException":
                    print("Аварийная остановка: мышь в углу экрана.")
                else:
                    print(f"Ошибка: {type(exc).__name__}: {exc}")
            finally:
                if hide:
                    self.call_ui(self.root.deiconify)
                self.busy.clear()
                self.events.put(("state", "idle"))


def main() -> None:
    if sys.platform == "win32":  # чёткий текст на экранах с масштабом
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
