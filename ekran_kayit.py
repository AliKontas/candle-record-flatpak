#!/usr/bin/env python3
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gtk, WebKit2, GLib, Gdk
import subprocess, os, datetime, shutil, json, threading, math
import cairo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

APP_DIR = os.path.dirname(os.path.abspath(__file__))
UI_PATH = os.path.join(APP_DIR, "ui.html")
DOWNLOADS = os.path.join(os.path.expanduser("~"), "Downloads", "Candle Record")
os.makedirs(DOWNLOADS, exist_ok=True)


def screen_size():
    try:
        screen = Gdk.Screen.get_default()
        if screen:
            return f"{screen.get_width()}x{screen.get_height()}"
    except Exception:
        pass
    return "1920x1080"


class State:
    def __init__(self):
        self.proc = None
        self.start_ts = None
        self.out_path = None
        self.last_file = None
        self.error = None
        self.size = screen_size()
        self.lock = threading.Lock()

    def status(self):
        with self.lock:
            elapsed = 0
            if self.proc and self.start_ts:
                elapsed = (datetime.datetime.now() - self.start_ts).total_seconds()
            return {
                "recording": bool(self.proc and self.proc.poll() is None),
                "elapsed": elapsed,
                "last_file": self.last_file,
                "error": self.error,
                "path": DOWNLOADS,
                "size": self.size,
            }

    def _default_monitor(self):
        try:
            sink = subprocess.check_output(["pactl", "get-default-sink"], text=True, stderr=subprocess.DEVNULL).strip()
            if sink:
                return sink + ".monitor"
        except Exception:
            pass
        return "default"

    def start(self, audio=False, resolution="native", quality="high"):
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return {"ok": False, "error": "Already recording."}
            if not shutil.which("ffmpeg"):
                return {"ok": False, "error": "ffmpeg not found."}
            self.error = None
            ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.out_path = os.path.join(DOWNLOADS, f"candle-record-{ts}.mp4")

            quality_presets = {
                "standard": ("veryfast", "23"),
                "high": ("medium", "18"),
                "ultra": ("slow", "16"),
            }
            preset, crf = quality_presets.get(quality, ("medium", "18"))

            cmd = [
                "ffmpeg", "-y",
                "-f", "x11grab",
                "-video_size", self.size,
                "-framerate", "30",
                "-i", os.environ.get("DISPLAY", ":0.0"),
            ]
            vf = []
            if resolution in ("1920x1080", "1280x720"):
                vf = ["-vf", f"scale={resolution}:flags=lanczos"]

            if audio:
                monitor = self._default_monitor()
                cmd += ["-f", "pulse", "-i", monitor]
                cmd += ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]
                cmd += vf
                cmd += ["-c:a", "aac", "-b:a", "192k"]
            else:
                cmd += ["-c:v", "libx264", "-preset", preset, "-crf", crf, "-pix_fmt", "yuv420p"]
                cmd += vf
                cmd += ["-an"]
            cmd += [self.out_path]
            try:
                self.proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self.start_ts = datetime.datetime.now()
                return {"ok": True}
            except Exception as e:
                self.proc = None
                self.error = f"Could not start: {e}"
                return {"ok": False, "error": self.error}

    def stop(self):
        with self.lock:
            proc = self.proc
            out = self.out_path
        if not proc or proc.poll() is not None:
            with self.lock:
                self.proc = None
            return {"ok": True}

        try:
            if proc.stdin:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        with self.lock:
            self.proc = None
            self.start_ts = None
            if out and os.path.exists(out) and os.path.getsize(out) > 0:
                self.last_file = os.path.basename(out)
                self.error = None
            else:
                self.error = "Recording ended unexpectedly."
        return {"ok": True}


STATE = State()
WIN = None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body if isinstance(body, (bytes, bytearray)) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        p = urlparse(self.path).path
        if p == "/" or p == "/index.html":
            try:
                with open(UI_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except Exception as e:
                self._send(500, f"UI load error: {e}", "text/plain; charset=utf-8")
            return
        if p == "/icon.png":
            icon_path = os.path.join(APP_DIR, "icon.png")
            try:
                with open(icon_path, "rb") as f:
                    self._send(200, f.read(), "image/png")
            except Exception:
                self._send(404, b"not found", "text/plain")
            return
        if p == "/start.mp3":
            sp = os.path.join(APP_DIR, "start.mp3")
            try:
                with open(sp, "rb") as f:
                    self._send(200, f.read(), "audio/mpeg")
            except Exception:
                self._send(404, b"not found", "text/plain")
            return
        if p == "/win/minimize":
            if WIN: GLib.idle_add(WIN.iconify)
            return self._send(200, '{"ok":true}')
        if p == "/win/maximize":
            def _t():
                if not WIN: return False
                if WIN.is_maximized(): WIN.unmaximize()
                else: WIN.maximize()
                return False
            if WIN: GLib.idle_add(_t)
            return self._send(200, '{"ok":true}')
        if p == "/win/fullscreen":
            def _f():
                if not WIN: return False
                if WIN.is_fullscreen(): WIN.unfullscreen()
                else: WIN.fullscreen()
                return False
            if WIN: GLib.idle_add(_f)
            return self._send(200, '{"ok":true}')
        if p == "/win/close":
            if WIN: GLib.idle_add(WIN.destroy)
            return self._send(200, '{"ok":true}')
        if p == "/win/drag":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k,v = part.split("=",1)
                    params[k]=v
            try:
                x = int(float(params.get("x","0")))
                y = int(float(params.get("y","0")))
            except Exception:
                x,y=0,0
            def _d():
                if not WIN: return False
                try:
                    WIN.begin_move_drag(1, x, y, Gdk.CURRENT_TIME)
                except Exception:
                    pass
                return False
            if WIN: GLib.idle_add(_d)
            return self._send(200, '{"ok":true}')
        if p == "/status":
            return self._send(200, json.dumps(STATE.status()))
        if p == "/start":
            qs = urlparse(self.path).query
            params = {}
            for part in qs.split("&"):
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k] = v
            audio = params.get("audio", "0") in ("1", "true")
            resolution = params.get("resolution", "native")
            quality = params.get("quality", "high")
            return self._send(200, json.dumps(STATE.start(audio=audio, resolution=resolution, quality=quality)))
        if p == "/stop":
            return self._send(200, json.dumps(STATE.stop()))
        if p == "/open-downloads":
            subprocess.Popen(["xdg-open", DOWNLOADS])
            return self._send(200, json.dumps({"ok": True}))
        self._send(404, '{"ok":false}')


def start_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


class Window(Gtk.Window):
    def __init__(self, url):
        super().__init__(title="Candle Record")
        self.set_default_size(1020, 700)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_resizable(True)
        self.set_size_request(860, 560)
        self.set_decorated(False)
        self.set_app_paintable(True)
        self.connect("size-allocate", self._apply_rounded_shape)
        try:
            self.set_wmclass("candle-record", "Candle Record")
        except Exception:
            pass
        icon_path = os.path.join(APP_DIR, "icon.png")
        if os.path.exists(icon_path):
            try:
                self.set_icon_from_file(icon_path)
            except Exception:
                pass

        web = WebKit2.WebView()
        web.set_background_color(Gdk.RGBA(7/255.0, 17/255.0, 31/255.0, 1.0))
        settings = web.get_settings()
        settings.set_enable_developer_extras(False)
        settings.set_enable_smooth_scrolling(True)
        web.load_uri(url)
        self.add(web)
        self.web = web
        self.connect("destroy", self.on_destroy)

    def _apply_rounded_shape(self, *_):
        # Clip the real X11 window to the rounded corners so no rectangular
        # WebView box shows behind the rounded UI.
        win = self.get_window()
        if not win:
            return False
        if Gtk.Window.is_maximized(self) or self.is_fullscreen():
            try:
                win.shape_combine_region(None, 0, 0)
            except Exception:
                pass
            return False
        try:
            w, h = self.get_size()
            r = 18
            surface = cairo.ImageSurface(cairo.FORMAT_A1, w, h)
            cr = cairo.Context(surface)
            cr.set_operator(cairo.OPERATOR_CLEAR)
            cr.paint()
            cr.set_operator(cairo.OPERATOR_SOURCE)
            cr.set_source_rgba(1, 1, 1, 1)
            cr.new_sub_path()
            cr.arc(r, r, r, math.pi, 1.5 * math.pi)
            cr.arc(w - r, r, r, 1.5 * math.pi, 2 * math.pi)
            cr.arc(w - r, h - r, r, 0, 0.5 * math.pi)
            cr.arc(r, h - r, r, 0.5 * math.pi, math.pi)
            cr.close_path()
            cr.fill()
            region = Gdk.cairo_region_create_from_surface(surface)
            win.shape_combine_region(region, 0, 0)
        except Exception:
            pass
        return False

    def is_fullscreen(self):
        w = self.get_window()
        if not w:
            return False
        return bool(w.get_state() & Gdk.WindowState.FULLSCREEN)

    def on_destroy(self, *_):
        try:
            STATE.stop()
        except Exception:
            pass
        Gtk.main_quit()


if __name__ == "__main__":
    GLib.set_prgname("candle-record")
    GLib.set_application_name("Candle Record")
    srv = start_server()
    port = srv.server_address[1]
    WIN = Window(f"http://127.0.0.1:{port}/")
    WIN.show_all()
    WIN.present()
    Gtk.main()
    try:
        srv.shutdown()
    except Exception:
        pass
