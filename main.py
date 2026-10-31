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
import tempfile
import time
import urllib.request
import uuid
import zipfile

import decky  # provided by Decky Loader

HOST = "127.0.0.1"
PORT = 8178
REPO = "ubermoc/Ubermoc-Speech"
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/tags/latest"
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
    "combo": {"lower": 0x10000, "upper": 0x400, "ids": []},  # R5 + R4 (see src/buttons.ts)
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
        self._migrate_old_models()
        self.settings = self._load_settings()
        self.want_running = False
        decky.logger.info("Ubermoc Speech loaded")
        if self.settings.get("autostart"):
            await Plugin.start_server(self)
        self.watchdog_task = asyncio.get_event_loop().create_task(Plugin._watchdog(self))

    async def _unload(self):
        task = getattr(self, "watchdog_task", None)
        if task:
            task.cancel()
        self.want_running = False
        await Plugin.stop_server(self)
        self._stop_recorder()

    async def _watchdog(self):
        """Restart the whisper server if it dies while it is supposed to run."""
        failures = 0
        while True:
            await asyncio.sleep(10)
            if not self.want_running:
                failures = 0
                continue
            if self.server_proc and self.server_proc.poll() is None:
                failures = 0
                continue
            failures += 1
            if failures > 5:
                decky.logger.error("whisper-server keeps crashing; watchdog giving up")
                self.want_running = False
                continue
            decky.logger.warning(f"whisper-server died; restarting (attempt {failures})")
            await asyncio.sleep(2 ** failures)
            await Plugin.start_server(self)

    # ---------- paths / settings ----------
    def _models_dir(self) -> str:
        # Deliberately NOT inside the plugin or Decky runtime dirs: those can be
        # wiped when the plugin is updated, reinstalled or uninstalled. A folder
        # in the user's home survives all of that, so a model is downloaded once.
        home = getattr(decky, "DECKY_USER_HOME", "/home/deck")
        return os.path.join(home, ".local", "share", "ubermoc-speech", "models")

    def _migrate_old_models(self):
        """Move models saved by earlier versions into the durable folder."""
        old = os.path.join(decky.DECKY_PLUGIN_RUNTIME_DIR, "models")
        if not os.path.isdir(old):
            return
        for fname in os.listdir(old):
            if fname.endswith(".bin"):
                dst = os.path.join(self._models_dir(), fname)
                if not os.path.exists(dst):
                    try:
                        shutil.move(os.path.join(old, fname), dst)
                    except OSError as e:
                        decky.logger.error(f"model migration failed: {e}")

    def _manifest_file(self) -> str:
        return os.path.join(self._models_dir(), "manifest.json")

    def _manifest(self) -> dict:
        try:
            with open(self._manifest_file()) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _model_ok(self, name: str) -> bool:
        """A model counts as installed only if it is complete."""
        path = self._model_path(name)
        if not os.path.exists(path):
            return False
        size = os.path.getsize(path)
        expected = self._manifest().get(name)
        if expected:
            return size == expected
        return size > MODELS.get(name, 0) * 1024 * 1024 * 0.9

    def _model_path(self, name: str) -> str:
        return os.path.join(self._models_dir(), f"ggml-{name}.bin")

    async def log(self, message: str) -> bool:
        decky.logger.info(f"[ui] {message}")
        return True

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
                "installed": self._model_ok(name),
            }
            for name, size in MODELS.items()
        ]

    async def download_model(self, name: str) -> dict:
        if name not in MODELS:
            return {"ok": False, "error": "unknown model"}
        if self._model_ok(name):
            return {"ok": True}  # never re-download a good model
        dest = self._model_path(name)
        tmp = dest + ".part"
        url = f"{MODEL_BASE_URL}/ggml-{name}.bin"
        self.download_progress[name] = 0.0

        def work():
            # A broken/partial final file is replaced; a .part file is resumed.
            if os.path.exists(dest):
                os.remove(dest)
            have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            headers = {"User-Agent": "ubermoc-speech"}
            if have:
                headers["Range"] = f"bytes={have}-"
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, context=_ssl_context(), timeout=60) as r:
                resumed = r.status == 206
                if have and not resumed:
                    have = 0  # server ignored Range; start over
                total = have + int(r.headers.get("Content-Length") or 0)
                done = have
                with open(tmp, "ab" if resumed else "wb") as out:
                    while chunk := r.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        if total:
                            self.download_progress[name] = done / total
            if total and os.path.getsize(tmp) != total:
                raise IOError("download incomplete; tap download again to resume")
            os.replace(tmp, dest)
            manifest = self._manifest()
            manifest[name] = os.path.getsize(dest)
            with open(self._manifest_file(), "w") as f:
                json.dump(manifest, f)

        try:
            await asyncio.get_event_loop().run_in_executor(None, work)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"download failed: {e}")
            # keep the .part file so the next attempt resumes
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
        if not self._model_ok(self.settings["model"]):
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
        self.want_running = True
        return {"ok": True, "running": True}

    async def stop_server(self) -> dict:
        self.want_running = False
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

    # ---------- self-update ----------
    def _installed_version(self) -> str:
        try:
            with open(os.path.join(decky.DECKY_PLUGIN_DIR, "VERSION")) as f:
                return f.read().strip()
        except OSError:
            return "unknown"

    def _fetch_release(self) -> dict:
        req = urllib.request.Request(
            RELEASE_API,
            headers={"User-Agent": "ubermoc-speech", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=20) as r:
            return json.loads(r.read().decode())

    async def check_update(self) -> dict:
        installed = self._installed_version()
        try:
            rel = await asyncio.get_event_loop().run_in_executor(None, self._fetch_release)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "installed": installed, "error": f"could not reach GitHub: {e}"}
        body = rel.get("body") or ""
        latest = body.rsplit(" ", 1)[-1].strip() if "Automatic build of" in body else ""
        asset = next((a for a in rel.get("assets", []) if a["name"].endswith(".zip")), None)
        if not latest or not asset:
            return {"ok": False, "installed": installed, "error": "no build found on GitHub"}
        return {
            "ok": True,
            "installed": installed,
            "latest": latest,
            "available": latest != installed,
        }

    def _download_and_stage(self) -> tuple[str, str]:
        rel = self._fetch_release()
        asset = next(a for a in rel["assets"] if a["name"].endswith(".zip"))
        latest = rel["body"].rsplit(" ", 1)[-1].strip()
        work = tempfile.mkdtemp(prefix="ubermoc-update-")
        zpath = os.path.join(work, "update.zip")
        req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "ubermoc-speech"})
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=120) as r, open(zpath, "wb") as out:
            shutil.copyfileobj(r, out)
        if os.path.getsize(zpath) != asset["size"]:
            raise IOError("update download incomplete")
        extract = os.path.join(work, "x")
        with zipfile.ZipFile(zpath) as z:
            if z.testzip() is not None:
                raise IOError("update zip is corrupt")
            root = os.path.realpath(extract)
            for name in z.namelist():  # refuse path traversal
                if not os.path.realpath(os.path.join(root, name)).startswith(root + os.sep):
                    raise IOError("unsafe path in update zip")
            z.extractall(extract)
        top = os.path.join(extract, os.listdir(extract)[0])
        for need in ("main.py", "plugin.json", os.path.join("dist", "index.js")):
            if not os.path.exists(os.path.join(top, need)):
                raise IOError(f"update is missing {need}")
        return top, latest

    def _apply(self, top: str):
        """Overwrite plugin files; every file goes via a temp name + atomic rename."""
        dest_root = decky.DECKY_PLUGIN_DIR
        backup = os.path.join(os.path.dirname(self._models_dir()), "previous-version")
        shutil.rmtree(backup, ignore_errors=True)
        shutil.copytree(dest_root, backup, symlinks=True)  # kept for manual rollback
        for dirpath, _dirs, files in os.walk(top):
            rel = os.path.relpath(dirpath, top)
            target_dir = os.path.join(dest_root, rel) if rel != "." else dest_root
            os.makedirs(target_dir, exist_ok=True)
            for fname in files:
                src = os.path.join(dirpath, fname)
                dst = os.path.join(target_dir, fname)
                tmp = dst + ".new"
                shutil.copy2(src, tmp)
                os.replace(tmp, dst)

    async def install_update(self) -> dict:
        try:
            top, latest = await asyncio.get_event_loop().run_in_executor(None, self._download_and_stage)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"update download failed: {e}")
            return {"ok": False, "error": str(e)}
        try:
            await Plugin.stop_server(self)
            await asyncio.get_event_loop().run_in_executor(None, self._apply, top)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"update apply failed: {e}")
            return {"ok": False, "error": str(e)}
        # Restart Decky shortly so the new backend and frontend are loaded.
        subprocess.Popen(
            ["sh", "-c", "sleep 3; systemctl restart plugin_loader"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
        )
        return {"ok": True, "version": latest}

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
