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
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"
MODEL_BASE_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
# name -> (approx size in MB, description). The file is ggml-<name>.bin.
MODELS = {
    "tiny.en": (75, "English only. Fastest, least accurate."),
    "base.en": (142, "English only. Fast, good for short phrases."),
    "base.en-q5_1": (57, "English only. Compressed base, smaller and a bit faster."),
    "small.en": (466, "English only. Accurate, a good balance."),
    "small.en-q5_1": (181, "English only. Compressed small, about the same accuracy."),
    "medium.en": (1533, "English only. Very accurate but slow on handhelds."),
    "medium.en-q5_0": (514, "English only. Compressed medium."),
    "tiny": (75, "All languages. Fastest, least accurate."),
    "base": (142, "All languages. Fast."),
    "small": (466, "All languages. Accurate."),
    "medium": (1533, "All languages. Very accurate but slow."),
    "large-v3-turbo-q5_0": (547, "All languages. Near-best accuracy, compressed."),
    "large-v3-turbo": (1624, "All languages. Best accuracy, heaviest."),
}

DEFAULT_SETTINGS = {
    "model": "base.en",
    "language": "en",
    "autostart": True,
    # "auto": keep the punctuation Whisper adds. "spoken": ignore Whisper's own
    # punctuation and only type what you say aloud ("period", "comma", ...).
    "punctuation": "auto",
    "indicator": True,   # popup notification while listening / transcribing
    "rumble": True,      # light haptic pulse when listening starts and ends
    # Steam button ids 33 + 45 = both right back buttons (see src/buttons.ts).
    "combo": {"lower": 0, "upper": 0, "ids": [33, 45]},
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


SPOKEN_MARKS = [
    # (spoken phrase, text to type, attach_left, ends_sentence)
    (r"question mark", "?", True, True),
    (r"exclamation (?:mark|point)", "!", True, True),
    (r"full stop", ".", True, True),
    (r"period", ".", True, True),
    (r"comma", ",", True, False),
    (r"semi-?colon", ";", True, False),
    (r"colon", ":", True, False),
    (r"ellipsis", "...", True, False),
    (r"open (?:paren|parenthesis|bracket)", "(", False, False),
    (r"close (?:paren|parenthesis|bracket)", ")", True, False),
    (r"open quote", '"', False, False),
    (r"(?:close|end) quote", '"', True, False),
    (r"new paragraph", "\n\n", True, True),
    (r"new line", "\n", True, True),
]


def apply_punctuation(text: str, mode: str) -> str:
    """auto: leave Whisper's output alone. spoken: type only dictated punctuation."""
    import re

    if mode != "spoken":
        return text
    # 1. Whisper capitalises after the punctuation it guessed. Lowercase those
    #    words (except "I" forms) before the guessed punctuation is removed.
    def relower(m):
        word = m.group(2)
        if re.match(r"I(?:'(?:m|ll|ve|d))?$", word):
            return m.group(0)
        return m.group(1) + word[0].lower() + word[1:]
    text = re.sub(r"([.!?]\s+)([A-Z][\w']*)", relower, text)
    # 2. Drop punctuation Whisper added on its own.
    text = re.sub(r"[.,;:!?]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # 3. Turn spoken punctuation into symbols.
    tokens = [(re.compile(rf"\b{pat}\b", re.I), sym, left, end) for pat, sym, left, end in SPOKEN_MARKS]
    marker = "\x00{}\x00"
    for i, (rx, sym, left, end) in enumerate(tokens):
        text = rx.sub(marker.format(i), text)
    out = ""
    cap_next = False
    for piece in re.split(r"(\x00\d+\x00)", text):
        if not piece:
            continue
        m = re.fullmatch(r"\x00(\d+)\x00", piece)
        if m:
            _rx, sym, left, end = tokens[int(m.group(1))]
            if left:
                out = out.rstrip(" ")
                out += sym + ("" if sym.startswith("\n") else " ")
            else:
                out += sym
            cap_next = cap_next or end
            continue
        piece = piece.strip(" ") if out.endswith(("(", "\n", '"')) else piece
        if cap_next:
            stripped = piece.lstrip()
            lead = piece[: len(piece) - len(stripped)]
            piece = lead + (stripped[:1].upper() + stripped[1:])
            cap_next = False if stripped else True
        out += piece
    out = re.sub(r" +", " ", out).strip()
    return re.sub(r"^([^A-Za-z]*)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)


def _int(v, default, lo=None, hi=None):
    if isinstance(v, bool) or not isinstance(v, int):
        return default
    if lo is not None and v < lo:
        return default
    if hi is not None and v > hi:
        return default
    return v


def _key(v, default):
    if not isinstance(v, dict) or v.get("mode") not in ("none", "enter", "custom"):
        return dict(default)
    mods = v.get("mods", [])
    mods = [m for m in mods if isinstance(m, int) and not isinstance(m, bool)] if isinstance(mods, list) else []
    return {"mode": v["mode"], "code": _int(v.get("code"), default["code"], 0, 255), "mods": mods}


def sanitize_settings(data) -> dict:
    """Settings files from older builds may have a different shape. Keep what is
    valid and fall back to defaults for the rest, so the panel can never crash
    on a stale file."""
    d = data if isinstance(data, dict) else {}
    out = dict(DEFAULT_SETTINGS)
    if d.get("model") in MODELS:
        out["model"] = d["model"]
    if isinstance(d.get("language"), str):
        out["language"] = d["language"]
    if isinstance(d.get("autostart"), bool):
        out["autostart"] = d["autostart"]
    for flag in ("indicator", "rumble"):
        if isinstance(d.get(flag), bool):
            out[flag] = d[flag]
    if d.get("punctuation") in ("auto", "spoken"):
        out["punctuation"] = d["punctuation"]
    c = d.get("combo")
    if isinstance(c, dict):
        ids = c.get("ids", [])
        ids = [i for i in ids if isinstance(i, int) and not isinstance(i, bool)] if isinstance(ids, list) else []
        out["combo"] = {
            "lower": _int(c.get("lower"), 0, 0),
            "upper": _int(c.get("upper"), 0, 0),
            "ids": ids,
        }
        if not (out["combo"]["lower"] or out["combo"]["upper"] or ids):
            out["combo"] = dict(DEFAULT_SETTINGS["combo"])
    out["before_key"] = _key(d.get("before_key"), DEFAULT_SETTINGS["before_key"])
    out["after_key"] = _key(d.get("after_key"), DEFAULT_SETTINGS["after_key"])
    profiles = {}
    raw = d.get("profiles")
    if isinstance(raw, dict):
        for appid, p in raw.items():
            if isinstance(p, dict) and isinstance(p.get("name"), str):
                prof = {
                    "name": p["name"],
                    "before_key": _key(p.get("before_key"), out["before_key"]),
                    "after_key": _key(p.get("after_key"), out["after_key"]),
                }
                pc = p.get("combo")
                if isinstance(pc, dict):
                    pids = pc.get("ids", [])
                    pids = [i for i in pids if isinstance(i, int) and not isinstance(i, bool)] if isinstance(pids, list) else []
                    prof["combo"] = {"lower": _int(pc.get("lower"), 0, 0), "upper": _int(pc.get("upper"), 0, 0), "ids": pids}
                profiles[str(appid)] = prof
    out["profiles"] = profiles
    return out


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
        return size > MODELS.get(name, (0, ""))[0] * 1024 * 1024 * 0.6

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
        return sanitize_settings(data)

    async def get_settings(self) -> dict:
        return self.settings

    async def set_settings(self, settings: dict) -> dict:
        self.settings = sanitize_settings(settings)
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
                "description": desc,
                "installed": self._model_ok(name),
            }
            for name, (size, desc) in MODELS.items()
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
               "-t", str(min(4, os.cpu_count() or 4))]
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
    def _user_uid(self) -> int:
        """UID of the desktop user (Decky itself runs as root). Not always 1000
        outside the Steam Deck, so read it from the owner of their home dir."""
        try:
            return os.stat(getattr(decky, "DECKY_USER_HOME", "/home/deck")).st_uid
        except OSError:
            return 1000

    def _audio_env(self) -> dict:
        uid = str(self._user_uid())
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
            text = json.loads(r.read().decode()).get("text", "").strip()
        return apply_punctuation(text, self.settings.get("punctuation", "auto"))

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

    # ---------- clipboard ----------
    async def set_clipboard(self, text: str) -> dict:
        """Best-effort system clipboard. Returns which method worked."""
        env = dict(os.environ)
        env.setdefault("DISPLAY", ":0")
        env.setdefault("WAYLAND_DISPLAY", "wayland-0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{self._user_uid()}")
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
