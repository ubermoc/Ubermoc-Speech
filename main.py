"""Ubermoc Speech - Decky Loader backend.

Manages a local whisper.cpp server, records microphone audio on demand,
transcribes it, and hands the text to the frontend / clipboard.
"""
import asyncio
import json
import mimetypes
import os
import shutil
import signal
import ssl
import subprocess
import time
import urllib.request
import uuid

import decky  # provided by Decky Loader

HOST = "127.0.0.1"
PORT = 8178
MODEL_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
MODELS = {
    "tiny.en": 75,
    "base.en": 142,
    "small.en": 466,
    "tiny": 75,
    "base": 142,
    "small": 466,
}

DEFAULT_SETTINGS = {
    "model": "base.en",
    "language": "en",
    "threads": 4,
    "autostart": True,
    # Combo is stored as raw controller bitmasks so any button set can be recorded.
    "combo": {"lower": 0x10000, "upper": 0x400},  # R5 + R4 (see src/buttons.ts)
    "before_key": {"mode": "enter", "code": 40},   # none | enter | custom
    "after_key": {"mode": "enter", "code": 40},
    "profiles": {},  # appid -> partial settings overriding the globals
}

CA_CANDIDATES = [
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ca-certificates/extracted/tls-ca-bundle.pem",
    "/etc/ssl/cert.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
]


def _ssl_context() -> ssl.SSLContext:
    for path in CA_CANDIDATES:
        if os.path.exists(path):
            return ssl.create_default_context(cafile=path)
    return ssl.create_default_context()


class Plugin:
    server_proc: subprocess.Popen | None = None
    record_proc: subprocess.Popen | None = None
    record_path: str | None = None
    download_progress: dict = {}

    # ---------- lifecycle ----------
    async def _main(self):
        os.makedirs(self._models_dir(), exist_ok=True)
        self.settings = self._load_settings()
        decky.logger.info("Ubermoc Speech loaded")
        if self.settings.get("autostart"):
            model = self._model_path(self.settings["model"])
            if os.path.exists(model):
                await Plugin.start_server(self)

    async def _unload(self):
        await Plugin.stop_server(self)
        self._stop_recorder()

    # ---------- paths / settings ----------
    def _models_dir(self) -> str:
        return os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "models")

    def _model_path(self, name: str) -> str:
        return os.path.join(self._models_dir(), f"ggml-{name}.bin")

    def _settings_file(self) -> str:
        return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "settings.json")

    def _load_settings(self) -> dict:
        data = {}
        try:
            with open(self._settings_file()) as f:
                data = json.load(f)
        except (OSError, ValueError):
            pass
        merged = {**DEFAULT_SETTINGS, **data}
        return merged

    async def get_settings(self) -> dict:
        return self.settings

    async def set_settings(self, settings: dict) -> dict:
        self.settings = {**DEFAULT_SETTINGS, **settings}
        os.makedirs(decky.DECKY_PLUGIN_SETTINGS_DIR, exist_ok=True)
        with open(self._settings_file(), "w") as f:
            json.dump(self.settings, f, indent=2)
        return self.settings

    # ---------- models ----------
    async def list_models(self) -> list:
        return [
            {
                "name": name,
                "size_mb": size,
                "installed": os.path.exists(self._model_path(name)),
            }
            for name, size in MODELS.items()
        ]

    async def download_model(self, name: str) -> dict:
        if name not in MODELS:
            return {"ok": False, "error": "unknown model"}
        dest = self._model_path(name)
        tmp = dest + ".part"
        url = f"{MODEL_BASE_URL}/ggml-{name}.bin"
        self.download_progress[name] = 0.0

        def work():
            req = urllib.request.Request(url, headers={"User-Agent": "ubermoc-speech"})
            with urllib.request.urlopen(req, context=_ssl_context(), timeout=60) as r, open(tmp, "wb") as out:
                total = int(r.headers.get("Content-Length") or 0)
                done = 0
                while chunk := r.read(1 << 20):
                    out.write(chunk)
                    done += len(chunk)
                    if total:
                        self.download_progress[name] = done / total
            os.replace(tmp, dest)

        try:
            await asyncio.get_event_loop().run_in_executor(None, work)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"download failed: {e}")
            if os.path.exists(tmp):
                os.remove(tmp)
            self.download_progress.pop(name, None)
            return {"ok": False, "error": str(e)}
        self.download_progress.pop(name, None)
        return {"ok": True}

    async def get_download_progress(self) -> dict:
        return self.download_progress

    async def delete_model(self, name: str) -> bool:
        try:
            os.remove(self._model_path(name))
            return True
        except OSError:
            return False

    # ---------- whisper server ----------
    def _server_binary(self) -> str:
        return os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "whisper-server")

    async def start_server(self) -> dict:
        if self.server_proc and self.server_proc.poll() is None:
            return {"ok": True, "running": True}
        model = self._model_path(self.settings["model"])
        if not os.path.exists(model):
            return {"ok": False, "error": "model not installed"}
        binary = self._server_binary()
        if not os.path.exists(binary):
            return {"ok": False, "error": "whisper-server binary missing"}
        os.chmod(binary, 0o755)
        env = {**os.environ, "LD_LIBRARY_PATH": os.path.dirname(binary)}
        cmd = [binary, "-m", model, "--host", HOST, "--port", str(PORT),
               "-t", str(self.settings.get("threads", 4))]
        log = open(os.path.join(decky.DECKY_PLUGIN_LOG_DIR, "whisper-server.log"), "ab")
        self.server_proc = subprocess.Popen(cmd, stdout=log, stderr=log, env=env)
        return {"ok": True, "running": True}

    async def stop_server(self) -> dict:
        proc = self.server_proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.server_proc = None
        return {"ok": True, "running": False}

    async def status(self) -> dict:
        return {
            "running": bool(self.server_proc and self.server_proc.poll() is None),
            "recording": bool(self.record_proc and self.record_proc.poll() is None),
            "binary": os.path.exists(self._server_binary()),
        }

    # ---------- recording ----------
    def _audio_env(self) -> dict:
        uid = os.environ.get("DECKY_USER_ID") or "1000"
        runtime = f"/run/user/{uid}"
        env = dict(os.environ)
        env["XDG_RUNTIME_DIR"] = runtime
        env["PULSE_SERVER"] = f"unix:{runtime}/pulse/native"
        return env

    async def start_recording(self) -> dict:
        if self.record_proc and self.record_proc.poll() is None:
            return {"ok": True}
        tool = shutil.which("parecord") or shutil.which("pw-record")
        if not tool:
            return {"ok": False, "error": "no parecord/pw-record found"}
        self.record_path = f"/tmp/ubermoc-speech-{uuid.uuid4().hex}.wav"
        if os.path.basename(tool) == "parecord":
            cmd = [tool, "--file-format=wav", "--rate=16000", "--channels=1", self.record_path]
        else:
            cmd = [tool, "--rate", "16000", "--channels", "1", self.record_path]
        self.record_proc = subprocess.Popen(
            cmd, env=self._audio_env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return {"ok": True}

    def _stop_recorder(self):
        proc = self.record_proc
        if proc and proc.poll() is None:
            proc.send_signal(signal.SIGINT)  # lets the tool finalize the WAV header
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.record_proc = None

    async def stop_and_transcribe(self) -> dict:
        path = self.record_path
        self._stop_recorder()
        if not path or not os.path.exists(path) or os.path.getsize(path) < 2000:
            return {"ok": False, "error": "no audio captured"}
        started = time.time()
        try:
            text = await asyncio.get_event_loop().run_in_executor(None, self._transcribe, path)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"transcribe failed: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return {"ok": True, "text": text, "seconds": round(time.time() - started, 2)}

    def _transcribe(self, path: str) -> str:
        boundary = uuid.uuid4().hex
        fields = {
            "response_format": "json",
            "temperature": "0.0",
            "language": self.settings.get("language", "en"),
        }
        body = b""
        for k, v in fields.items():
            body += (f"--{boundary}\r\nContent-Disposition: form-data; "
                     f'name="{k}"\r\n\r\n{v}\r\n').encode()
        with open(path, "rb") as f:
            audio = f.read()
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                 f'filename="audio.wav"\r\nContent-Type: audio/wav\r\n\r\n').encode()
        body += audio + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/inference", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode()).get("text", "").strip()

    # ---------- clipboard ----------
    async def set_clipboard(self, text: str) -> dict:
        """Best-effort system clipboard. Returns which method worked."""
        env = dict(os.environ)
        env.setdefault("DISPLAY", ":0")
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.environ.get('DECKY_USER_ID', '1000')}")
        for name, cmd in (
            ("wl-copy", ["wl-copy"]),
            ("xclip", ["xclip", "-selection", "clipboard"]),
            ("xsel", ["xsel", "--clipboard", "--input"]),
        ):
            if not shutil.which(cmd[0]):
                continue
            try:
                p = subprocess.Popen(cmd, stdin=subprocess.PIPE, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                p.stdin.write(text.encode())
                p.stdin.close()
                await asyncio.sleep(0.15)
                if p.poll() in (None, 0):
                    return {"ok": True, "method": name}
            except OSError:
                continue
        return {"ok": False, "method": "frontend"}
