"""George in a pop-up window. Press Ctrl+Alt+Space anywhere to show or hide him.

He lives in the system tray (right-click the "G" icon for options). Start him
with `george popup`; add --hidden to start in the tray without showing the window.

How it fits together: the window runs on the main thread (Tkinter requires it),
George runs on his own asyncio loop in a background thread so his MCP
connections stay open, and the hotkey and tray icon each run on their own
thread. Other threads never touch the window directly; they hand it work
through a queue (PopupUI.post), because Tkinter isn't thread-safe.
"""

import asyncio
import ctypes
import json
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, simpledialog

from ctypes import wintypes

import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk

from mcp_tools import GEORGE_HOME

# The hotkey is registered with Windows itself (RegisterHotKey), so George only
# ever hears about this one key combination, never anything else you type.
HOTKEY_MODIFIERS = 0x0002 | 0x0001 | 0x4000  # MOD_CONTROL | MOD_ALT | MOD_NOREPEAT
HOTKEY_KEY = 0x20  # VK_SPACE
HOTKEY_LABEL = "Ctrl+Alt+Space"
WM_HOTKEY = 0x0312

# Lets a second `george popup` tell the running one to show itself.
SHOW_EVENT_NAME = "Local\\GeorgePopupShow"
ERROR_ALREADY_EXISTS = 183

# A dark theme, roughly matching VS Code.
BG, PANEL, FG, DIM = "#1e1e1e", "#252526", "#d4d4d4", "#808080"
YOU, GEORGE, ACCENT = "#4fc1ff", "#e5c07b", "#0e639c"

kernel32 = ctypes.windll.kernel32


class GeorgeWorker:
    """Runs George on his own asyncio loop in a background thread."""

    def __init__(self, ui: "PopupUI"):
        self.ui = ui
        self.session = None  # set once George has connected
        self.loop = asyncio.new_event_loop()
        self._stop: asyncio.Event | None = None
        self._thread = threading.Thread(target=self.loop.run_until_complete,
                                        args=(self._main(),), daemon=True)
        self._thread.start()

    async def _main(self):
        # Imported here, not at the top, so the window appears right away
        # instead of waiting for LangChain and the model to load.
        from george import open_george

        self._stop = asyncio.Event()
        try:
            async with open_george() as george:
                self.session = george
                self.ui.post(self.ui.on_connected, george.servers, george.failed)
                await self._stop.wait()  # stay connected until the app quits
        except Exception as error:
            self.ui.post(self.ui.on_error, f"George couldn't start: {type(error).__name__}: {error}")

    def send(self, prompt: str, thread_id: str):
        """Run one turn in the background; the reply goes to ui.on_reply."""
        from george import run_turn

        async def turn():
            try:
                result = await run_turn(
                    self.session.agent, prompt, {"configurable": {"thread_id": thread_id}},
                    self._review_tool_calls, self._answer_mcp_questions,
                )
                reply = result["messages"][-1].text
            except Exception as error:
                reply = f"(Something went wrong: {type(error).__name__}: {error})"
            self.ui.post(self.ui.on_reply, reply)

        asyncio.run_coroutine_threadsafe(turn(), self.loop)

    # George pauses on these while the window asks the user (in the UI thread).
    async def _review_tool_calls(self, request: dict) -> dict:
        return await asyncio.to_thread(self.ui.call, self.ui.review_tool_calls, request)

    async def _answer_mcp_questions(self, request: dict) -> dict:
        return await asyncio.to_thread(self.ui.call, self.ui.answer_mcp_questions, request)

    def stop(self):
        """Disconnect George (closing his MCP servers) and wait briefly for it."""
        if self._stop is not None:
            self.loop.call_soon_threadsafe(self._stop.set)
        self._thread.join(timeout=10)


class PopupUI:
    def __init__(self, start_hidden: bool):
        from memory import current_thread_id

        self.root = tk.Tk()
        self.root.title("George")
        self.root.configure(bg=BG)
        self.root.geometry(self._centered(760, 600))
        self.root.minsize(480, 360)
        self._icon = ImageTk.PhotoImage(george_icon())  # kept so Tk doesn't lose it
        self.root.iconphoto(True, self._icon)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)  # closing hides to the tray
        self.root.bind("<Escape>", lambda _: self.hide())

        self._queue: queue.Queue = queue.Queue()
        self.busy = False
        self.thread_id = current_thread_id()  # same conversation as the terminal
        self._build_widgets()
        self._say("dim", f"Connecting to George's services... ({HOTKEY_LABEL} shows or hides this window)")
        self.root.after(50, self._drain_queue)

        self.worker = GeorgeWorker(self)
        if start_hidden:
            self.root.withdraw()
        else:
            self.root.after(100, self.show)

    # ---- layout -------------------------------------------------------------

    def _centered(self, width: int, height: int) -> str:
        # Sizes are given for a 100% display; scale them for Windows display scaling.
        scale = self.root.winfo_fpixels("1i") / 96
        width, height = int(width * scale), int(height * scale)
        x = (self.root.winfo_screenwidth() - width) // 2
        y = (self.root.winfo_screenheight() - height) // 3
        return f"{width}x{height}+{x}+{y}"

    def _build_widgets(self):
        # The bottom bars are packed first so the transcript can't push them
        # off the window; it only gets the space that's left.
        self.status = tk.Label(self.root, text="Connecting...", anchor="w",
                               bg=PANEL, fg=DIM, padx=12, pady=4)
        self.status.pack(side="bottom", fill="x")

        bar = tk.Frame(self.root, bg=PANEL, padx=10, pady=10)
        bar.pack(side="bottom", fill="x")
        self.entry = tk.Entry(bar, font=("Segoe UI", 12), bg=BG, fg=FG,
                              insertbackground=FG, relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.entry.bind("<Return>", lambda _: self.submit())
        button = dict(bg=ACCENT, fg="white", activebackground="#1177bb",
                      activeforeground="white", relief="flat", padx=14, pady=4)
        tk.Button(bar, text="Send", command=self.submit, **button).pack(side="left")
        tk.Button(bar, text="New chat", command=self.new_chat, **button).pack(side="left", padx=(8, 0))

        self.transcript = scrolledtext.ScrolledText(
            self.root, wrap="word", state="disabled", font=("Consolas", 11), height=1,
            bg=BG, fg=FG, insertbackground=FG, relief="flat", padx=12, pady=10,
        )
        self.transcript.tag_config("you", foreground=YOU, font=("Consolas", 11, "bold"))
        self.transcript.tag_config("george", foreground=GEORGE, font=("Consolas", 11, "bold"))
        self.transcript.tag_config("dim", foreground=DIM)
        self.transcript.pack(fill="both", expand=True)

    def _say(self, tag: str, text: str):
        """Add a line to the transcript."""
        self.transcript.configure(state="normal")
        self.transcript.insert("end", text + "\n\n", tag)
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    # ---- talking to other threads ---------------------------------------------

    def post(self, fn, *args):
        """Run fn(*args) on the UI thread soon. Safe to call from any thread."""
        self._queue.put((fn, args))

    def call(self, fn, *args):
        """Run fn(*args) on the UI thread and wait for its result (from another thread)."""
        done, box = threading.Event(), {}

        def run():
            try:
                box["value"] = fn(*args)
            except Exception as error:
                box["error"] = error
            finally:
                done.set()

        self.post(run)
        done.wait()
        if "error" in box:
            raise box["error"]
        return box["value"]

    def _drain_queue(self):
        while True:
            try:
                fn, args = self._queue.get_nowait()
            except queue.Empty:
                break
            fn(*args)
        self.root.after(50, self._drain_queue)

    # ---- showing and hiding -------------------------------------------------

    def show(self):
        self.root.deiconify()
        self.root.lift()
        # Briefly force it on top so it appears over whatever app you're in.
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        self.entry.focus_set()

    def hide(self):
        self.root.withdraw()

    def toggle(self):
        visible = self.root.state() != "withdrawn"
        if visible and self.root.focus_displayof() is not None:
            self.hide()
        else:
            self.show()

    # ---- chatting -----------------------------------------------------------

    def submit(self):
        prompt = self.entry.get().strip()
        if not prompt or self.busy:
            return
        if self.worker.session is None:
            self.status.configure(text="George is still connecting. Give him a moment.")
            return
        self.entry.delete(0, "end")
        if prompt.lower() == "new":
            self.new_chat()
            return
        self._say("you", "You")
        self._say("", prompt)
        self.busy = True
        self.status.configure(text="George is thinking... (grumbling, mostly)")
        self.worker.send(prompt, self.thread_id)

    def new_chat(self):
        from memory import new_thread_id

        if self.busy:
            return
        self.thread_id = new_thread_id()
        self._say("dim", "Started a new conversation. Long-term memories are kept.")

    def on_reply(self, text: str):
        self._say("george", "George")
        self._say("", text)
        self.busy = False
        self.status.configure(text="Ready.")

    def on_connected(self, servers: list[str], failed: dict[str, str]):
        for name, error in failed.items():
            self._say("dim", f"Skipped {name}: {error}")
        self._say("dim", f"Connected: {', '.join(servers)}. Resuming your last conversation.")
        self.status.configure(text="Ready.")

    def on_error(self, message: str):
        self._say("dim", message)
        self.status.configure(text="Not connected. See the message above.")

    # ---- approvals (called through `call`, so George waits for the answer) ----

    def review_tool_calls(self, request: dict) -> dict:
        self.show()
        decisions = []
        for action in request["action_requests"]:
            args = json.dumps(action["args"], indent=2)
            if len(args) > 1500:
                args = args[:1500] + "\n..."
            allowed = messagebox.askyesno(
                "George wants to run a tool",
                f"{action['name']}\n\n{args}\n\nAllow it?", parent=self.root,
            )
            self._say("dim", f"{'Allowed' if allowed else 'Rejected'}: {action['name']}")
            decisions.append({"type": "approve"} if allowed
                             else {"type": "reject", "message": "The user said no."})
        return {"decisions": decisions}

    def answer_mcp_questions(self, request: dict) -> dict:
        from george import parse_answer

        self.show()
        responses = {}
        for question in request["requests"]:
            title = f"{request['tool_name']} asks"
            if question["mode"] == "url":
                done = messagebox.askyesno(
                    title, f"{question['message']}\n\nOpen this, then click Yes when done:\n"
                    f"{question['url']}", parent=self.root,
                )
                responses[question["key"]] = {"action": "accept" if done else "decline"}
                continue
            content = {}
            for name, field in question["requested_schema"].get("properties", {}).items():
                answer = simpledialog.askstring(title, f"{question['message']}\n\n{name}:",
                                                parent=self.root)
                if answer is None:  # Cancel
                    break
                content[name] = parse_answer(answer, field.get("type"))
            else:
                responses[question["key"]] = {"action": "accept", "content": content}
                continue
            responses[question["key"]] = {"action": "decline"}
        return {"responses": responses}

    # ---- quitting -----------------------------------------------------------

    def quit(self):
        self.hide()
        self.worker.stop()
        self.root.destroy()


def george_icon() -> Image.Image:
    """A gold circle with a "G", for the tray and the window's title bar."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, 62, 62), fill=GEORGE)
    draw.text((32, 33), "G", fill=BG, anchor="mm", font=ImageFont.load_default(size=40))
    return image


def _tray_icon(ui: PopupUI) -> pystray.Icon:
    image = george_icon()
    menu = pystray.Menu(
        pystray.MenuItem(f"Show George ({HOTKEY_LABEL})", lambda: ui.post(ui.show), default=True),
        pystray.MenuItem("New conversation", lambda: ui.post(ui.new_chat)),
        pystray.MenuItem("Quit George", lambda: ui.post(ui.quit)),
    )
    return pystray.Icon("george", image, "George", menu)


def _listen_for_hotkey(ui: PopupUI):
    """Toggle the window whenever Windows reports the hotkey was pressed."""
    user32 = ctypes.windll.user32
    if not user32.RegisterHotKey(None, 1, HOTKEY_MODIFIERS, HOTKEY_KEY):
        ui.post(ui._say, "dim", f"Couldn't claim {HOTKEY_LABEL}: another app is using it. "
                                "Use the tray icon instead.")
        return
    msg = wintypes.MSG()
    # Windows delivers the hotkey as a message to this thread.
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY:
            ui.post(ui.toggle)


def _listen_for_show_requests(ui: PopupUI, event_handle: int):
    """Show the window whenever another `george popup` signals the event."""
    while True:
        kernel32.WaitForSingleObject(event_handle, 0xFFFFFFFF)  # wait forever
        ui.post(ui.show)


def main():
    # Started with pythonw there is no console, so send stray output to a log.
    if sys.stdout is None:
        (GEORGE_HOME / "logs").mkdir(parents=True, exist_ok=True)
        log = open(GEORGE_HOME / "logs" / "popup.log", "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr = log

    # One George at a time: if he's already running, just ask him to show up.
    event = kernel32.CreateEventW(None, False, False, SHOW_EVENT_NAME)
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.SetEvent(event)
        return

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on high-DPI screens
    except (AttributeError, OSError):
        pass

    ui = PopupUI(start_hidden="--hidden" in sys.argv)
    threading.Thread(target=_listen_for_show_requests, args=(ui, event), daemon=True).start()
    threading.Thread(target=_listen_for_hotkey, args=(ui,), daemon=True).start()
    icon = _tray_icon(ui)
    icon.run_detached()
    try:
        ui.root.mainloop()
    finally:
        icon.stop()


if __name__ == "__main__":
    main()
