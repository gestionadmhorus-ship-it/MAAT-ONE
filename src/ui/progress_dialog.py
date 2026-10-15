"""Trabajo en segundo plano con aviso modal y avance en el hilo de Tk."""
import queue
import threading
import tkinter as tk
from tkinter import ttk

def run_task(parent, title, task):
    messages = queue.Queue()
    result = {}
    window = tk.Toplevel(parent)
    window.title(title)
    window.transient(parent)
    window.resizable(False, False)
    window.protocol("WM_DELETE_WINDOW", lambda: None)
    body = ttk.Frame(window, padding=24)
    body.pack(fill="both", expand=True)
    status = tk.StringVar(value=title)
    ttk.Label(body, textvariable=status, wraplength=440).pack(anchor="w")
    bar = ttk.Progressbar(body, length=440, mode="indeterminate")
    bar.pack(fill="x", pady=14)
    bar.start(12)
    ttk.Label(body, text="Espere, no cierre la aplicación mientras se procesa.").pack(anchor="w")
    window.update_idletasks()
    x=max(0,parent.winfo_rootx()+(parent.winfo_width()-window.winfo_width())//2)
    y=max(0,parent.winfo_rooty()+(parent.winfo_height()-window.winfo_height())//2)
    window.geometry(f"+{x}+{y}")
    previous = parent.grab_current()
    window.grab_set()
    def report(message, done=None, total=None):
        messages.put(("progress", (message, done, total)))
    def work():
        try: messages.put(("done", task(report)))
        except Exception as error: messages.put(("error", error))
    def poll():
        try:
            while True:
                kind, value = messages.get_nowait()
                if kind != "progress":
                    result[kind] = value
                    bar.stop(); window.destroy()
                    return
                message, done, total = value
                status.set(message)
                if total:
                    bar.stop(); bar.configure(mode="determinate", maximum=total, value=done or 0)
                else:
                    bar.configure(mode="indeterminate"); bar.start(12)
        except queue.Empty: pass
        window.after(60, poll)
    threading.Thread(target=work, daemon=True).start()
    window.after(60, poll)
    parent.wait_window(window)
    if previous is not None and previous.winfo_exists(): previous.grab_set()
    if "error" in result: raise result["error"]
    return result.get("done")
