"""Ubermoc Speech - Decky Loader backend.

Records from the microphone and transcribes it to text with a local
whisper.cpp server. Also contains the GitHub self-updater.
"""
import asyncio
import json
import os
import shutil
import signal
import socket
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
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"

MODEL_NAME = "base.en"
MODEL_MB = 142
MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-base.en.bin"

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
    download_progress: float | None = None

    # ---------- lifecycle ----------
    async def _main(self):
        os.makedirs(self._models_dir(), exist_ok=True)
        decky.logger.info("Ubermoc Speech loaded")

    async def _unload(self):
        self._stop_recorder()
        await Plugin.stop_server(self)

    async def log(self, message: str) -> bool:
        decky.logger.info(f"[ui] {message}")
        return True

    # ---------- paths ----------
    def _models_dir(self) -> str:
        # Outside the plugin folder so updates and reinstalls never delete it.
        home = getattr(decky, "DECKY_USER_HOME", "/home/deck")
        return os.path.join(home, ".local", "share", "ubermoc-speech", "models")

    def _model_path(self) -> str:
        return os.path.join(self._models_dir(), f"ggml-{MODEL_NAME}.bin")

    def _model_ok(self) -> bool:
        path = self._model_path()
        return os.path.exists(path) and os.path.getsize(path) > MODEL_MB * 1024 * 1024 * 0.9

    def _user_uid(self) -> int:
        try:
            return os.stat(getattr(decky, "DECKY_USER_HOME", "/home/deck")).st_uid
        except OSError:
            return 1000

    # ---------- model ----------
    async def download_model(self) -> dict:
        if self._model_ok():
            return {"ok": True}
        dest = self._model_path()
        tmp = dest + ".part"
        self.download_progress = 0.0

        def work():
            have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
            headers = {"User-Agent": "ubermoc-speech"}
            if have:
                headers["Range"] = f"bytes={have}-"
            req = urllib.request.Request(MODEL_URL, headers=headers)
            with urllib.request.urlopen(req, context=_ssl_context(), timeout=60) as r:
                resumed = r.status == 206
                if have and not resumed:
                    have = 0
                total = have + int(r.headers.get("Content-Length") or 0)
                done = have
                with open(tmp, "ab" if resumed else "wb") as out:
                    while chunk := r.read(1 << 20):
                        out.write(chunk)
                        done += len(chunk)
                        if total:
                            self.download_progress = done / total
            if total and os.path.getsize(tmp) != total:
                raise IOError("download incomplete; tap download again to resume")
            os.replace(tmp, dest)

        try:
            await asyncio.get_event_loop().run_in_executor(None, work)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"download failed: {e}")
            self.download_progress = None
            return {"ok": False, "error": str(e)}
        self.download_progress = None
        return {"ok": True}

    async def get_download_progress(self):
        return self.download_progress

    async def status(self) -> dict:
        binary = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "whisper-server")
        return {
            "model": self._model_ok(),
            "binary": os.path.exists(binary),
            "server": bool(self.server_proc and self.server_proc.poll() is None),
            "recording": bool(self.record_proc and self.record_proc.poll() is None),
        }

    # ---------- whisper server (started on first use) ----------
    async def start_server(self) -> dict:
        if self.server_proc and self.server_proc.poll() is None:
            return {"ok": True}
        if not self._model_ok():
            return {"ok": False, "error": "model not downloaded"}
        binary = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", "whisper-server")
        if not os.path.exists(binary):
            return {"ok": False, "error": "whisper-server binary missing"}
        os.chmod(binary, 0o755)
        env = {**os.environ, "LD_LIBRARY_PATH": os.path.dirname(binary)}
        cmd = [binary, "-m", self._model_path(), "--host", HOST, "--port", str(PORT),
               "-t", str(min(4, os.cpu_count() or 4))]
        os.makedirs(decky.DECKY_PLUGIN_LOG_DIR, exist_ok=True)
        log = open(os.path.join(decky.DECKY_PLUGIN_LOG_DIR, "whisper-server.log"), "ab")
        self.server_proc = subprocess.Popen(cmd, stdout=log, stderr=log, env=env)
        # wait until it accepts connections (model load takes a few seconds)
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.server_proc.poll() is not None:
                return {"ok": False, "error": "whisper-server exited; see its log"}
            try:
                with socket.create_connection((HOST, PORT), timeout=1):
                    return {"ok": True}
            except OSError:
                await asyncio.sleep(0.5)
        return {"ok": False, "error": "whisper-server did not start in time"}

    async def stop_server(self) -> dict:
        proc = self.server_proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.server_proc = None
        return {"ok": True}

    # ---------- recording + transcription ----------
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
        runtime = f"/run/user/{self._user_uid()}"
        env = {**os.environ, "XDG_RUNTIME_DIR": runtime, "PULSE_SERVER": f"unix:{runtime}/pulse/native"}
        self.record_proc = subprocess.Popen(
            cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
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
        try:
            started = await Plugin.start_server(self)
            if not started.get("ok"):
                return started
            text = await asyncio.get_event_loop().run_in_executor(None, self._transcribe, path)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"transcribe failed: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return {"ok": True, "text": text}

    def _transcribe(self, path: str) -> str:
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in {"response_format": "json", "temperature": "0.0", "language": "en"}.items():
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
    def _read_file(self, name: str) -> str:
        try:
            with open(os.path.join(decky.DECKY_PLUGIN_DIR, name)) as f:
                return f.read().strip()
        except OSError:
            return ""

    def _installed_info(self) -> dict:
        return {"version": self._read_file("VERSION") or "unknown", "build": self._read_file("BUILD")}

    def _fetch_release(self) -> dict:
        req = urllib.request.Request(
            RELEASE_API,
            headers={"User-Agent": "ubermoc-speech", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=20) as r:
            return json.loads(r.read().decode())

    @staticmethod
    def _parse_release(rel: dict) -> dict:
        """Release notes look like 'Version 0.01 build abc1234'."""
        import re

        body = rel.get("body") or ""
        m = re.search(r"Version\s+(\S+)\s+build\s+(\w+)", body)
        if m:
            return {"version": m.group(1), "build": m.group(2)}
        m = re.search(r"build(?: of)?\s+(\w+)", body)  # older releases
        return {"version": "unknown", "build": m.group(1) if m else ""}

    async def get_version(self) -> dict:
        return self._installed_info()

    async def check_update(self) -> dict:
        installed = self._installed_info()
        try:
            rel = await asyncio.get_event_loop().run_in_executor(None, self._fetch_release)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "installed": installed, "error": f"could not reach GitHub: {e}"}
        latest = self._parse_release(rel)
        asset = next((a for a in rel.get("assets", []) if a["name"].endswith(".zip")), None)
        if not latest["build"] or not asset:
            return {"ok": False, "installed": installed, "error": "no build found on GitHub"}
        return {
            "ok": True,
            "installed": installed,
            "latest": latest,
            "available": latest["build"] != installed["build"],
        }

    def _download_and_stage(self) -> tuple[str, str]:
        rel = self._fetch_release()
        asset = next(a for a in rel["assets"] if a["name"].endswith(".zip"))
        latest = self._parse_release(rel)["version"]
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
