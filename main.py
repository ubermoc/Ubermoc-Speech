# Copyright (c) 2026 ubermoc. All rights reserved.
# Proprietary and confidential. No licence is granted to copy, modify, redistribute, sublicense,
# train or fine-tune machine-learning models on, or otherwise reuse this file, in whole or in part,
# except as stated in the LICENSE file. Provenance id: ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c
"""Ubermoc Speech - Decky Loader backend.

Records from the microphone and transcribes it to text with a local
speech server (whisper.cpp, or NVIDIA Parakeet through parakeet.cpp). Also contains the GitHub self-updater.
"""
import asyncio
import json
import os
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
import zipfile

PROVENANCE = "ubermoc-speech/8fb7b6c1-05a7-4b68-908f-9d76c129be5c"  # see LICENSE
import decky  # provided by Decky Loader

# Decky already puts <plugin>/py_modules on sys.path; do it explicitly as well.
sys.path.insert(0, os.path.join(decky.DECKY_PLUGIN_DIR, "py_modules"))
import ubermoc_input  # noqa: E402
import ubermoc_audio  # noqa: E402
import ubermoc_models  # noqa: E402
import ubermoc_rumble  # noqa: E402
import ubermoc_settings  # noqa: E402
import ubermoc_typing  # noqa: E402
import ubermoc_voicetest  # noqa: E402

HOST = "127.0.0.1"
PORT = 8178
REPO = "ubermoc/Ubermoc-Speech"
VAD_MODEL = "ggml-silero-v6.2.0.bin"
PLUGIN_NAME = "Ubermoc Speech"
PLUGIN_FILES = ("plugin.json", "main.py", "dist/index.js")
RELEASE_API = f"https://api.github.com/repos/{REPO}/releases/latest"

MODELS = ubermoc_models.MODELS          # name -> (approx size in MB, description); see py_modules/ubermoc_models.py
DEFAULT_MODEL = ubermoc_models.DEFAULT_MODEL

TYPE_TEST_TEXT = "Ubermoc Speech test"

# Whisper normally encodes a full 30 s window even for a 3 s clip. Telling it the
# real length makes short recordings much faster. Set False to turn this off.
SHORT_AUDIO_CONTEXT = True
WAV_BYTES_PER_SECOND = 32000  # 16 kHz, mono, 16-bit

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
    server_model: str = ""       # the model the running server was started with
    server_task: "asyncio.Future | None" = None
    record_proc: subprocess.Popen | None = None
    record_path: str | None = None
    download_progress: dict = {}
    cancel_flags: set = set()
    # dictation state, shared by the panel button and the activator
    phase: str = "idle"            # idle | starting | recording | transcribing
    last_result: dict | None = None
    by_activator: bool = False
    act_down: bool = False
    hub = None
    keyboard = None                # the virtual keyboard, created on first use
    presser = None                 # presses keys / mouse buttons / controller buttons
    rumbler = None                 # buzzes the controllers when recording starts / stops
    panel_seen: float = 0.0        # last time the panel polled (it is open if recent)
    type_acks: dict = {}
    last_typing_test: dict | None = None
    running_app: dict | None = None  # {"appid", "name", "t"} reported by the panel's frontend

    # ---------- lifecycle ----------
    async def _main(self):
        os.makedirs(self._data_dir(), exist_ok=True)
        os.makedirs(self._models_dir(), exist_ok=True)
        self.hub = ubermoc_input.InputHub(
            asyncio.get_event_loop(), lambda active: Plugin._on_activator(self, active))
        settings = self._load_settings()
        tokens = ubermoc_input.valid_tokens(settings.get("activator"))
        if tokens:
            self.hub.set_activator(tokens)
        asyncio.ensure_future(Plugin._prepare_outputs_if_custom(self))
        decky.logger.info(
            f"Ubermoc Speech loaded: model={'set' if settings.get('model') else 'default'}, "
            f"activator={len(tokens)} input(s), settings from {self._store().paths}")

    async def _unload(self):
        if self.hub:
            self.hub.stop()
        if self.keyboard:
            self.keyboard.close()
        if self.presser:
            self.presser.close()
        self._stop_recorder()
        Plugin._vt_cleanup(self)
        await Plugin.stop_server(self)

    async def log(self, message: str) -> bool:
        decky.logger.info(f"[ui] {message}")
        return True

    # ---------- paths / settings ----------
    def _data_dir(self) -> str:
        # Outside the plugin folder so updates and reinstalls never delete it.
        home = getattr(decky, "DECKY_USER_HOME", "/home/deck")
        return os.path.join(home, ".local", "share", "ubermoc-speech")

    def _models_dir(self) -> str:
        return os.path.join(self._data_dir(), "models")

    def _model_path(self, name: str) -> str:
        return os.path.join(self._models_dir(), ubermoc_models.model_filename(name))

    def _model_ok(self, name: str) -> bool:
        path = self._model_path(name)
        if name not in MODELS or not os.path.exists(path):
            return False
        return os.path.getsize(path) > MODELS[name][0] * 1024 * 1024 * 0.6

    def _store(self):
        if getattr(self, "_settings_store", None) is None:
            paths = [os.path.join(self._data_dir(), "settings.json")]
            settings_dir = getattr(decky, "DECKY_PLUGIN_SETTINGS_DIR", "")
            if settings_dir:        # Decky's recommended place for settings
                paths.insert(0, os.path.join(settings_dir, "settings.json"))
            self._settings_store = ubermoc_settings.SettingsStore(
                paths, log=lambda m: decky.logger.warning(m))
        return self._settings_store

    def _load_settings(self) -> dict:
        return self._store().load()

    def _save_settings(self, **patch):
        """Merge into the stored settings (saving one never drops another)."""
        if self._store().save(**patch):
            decky.logger.info(f"settings saved ({', '.join(sorted(patch))})")

    def _selected(self) -> str:
        """The model in use. Always a downloaded one, or "" if none is."""
        return ubermoc_models.choose_model(self._load_settings().get("model"), self._model_ok)

    def _user_uid(self) -> int:
        try:
            return os.stat(getattr(decky, "DECKY_USER_HOME", "/home/deck")).st_uid
        except OSError:
            return 1000

    def _vad_on(self, name: str) -> bool:
        """Whether the voice detector is used with this model. It is on unless the user
        turned it off for that model."""
        if not ubermoc_models.vad_supported(name):
            return False           # Parakeet has no voice detector switch (it returns nothing for silence)
        chosen = self._load_settings().get("vad")
        if isinstance(chosen, dict) and isinstance(chosen.get(name), bool):
            return chosen[name]
        return True

    @staticmethod
    def _vad_threshold(name: str) -> str:
        """How sure the voice detector must be that it hears speech. Lower keeps quieter
        speech but lets more noise through. The large models make stray words out of
        noise, so they get the stricter value; the others' sound labels are already
        removed before typing, so they can afford the more sensitive one."""
        return "0.10" if name.startswith("large") else "0.08"

    # ---------- models ----------
    async def list_models(self) -> dict:
        return {
            "selected": self._selected(),
            "vad": self._vad_on(self._selected()),
            "models": ubermoc_models.visible_models(self._model_ok),
        }

    async def set_model(self, name: str) -> dict:
        if name not in MODELS or not self._model_ok(name):
            return {"ok": False, "error": "model is not downloaded"}
        self._save_selected(name)
        await Plugin.stop_server(self)  # restarts with the new model on next use
        return {"ok": True}

    async def set_vad(self, on: bool) -> dict:
        """Turn the voice detector on or off for the current model."""
        name = self._selected()
        if not name:
            return {"ok": False, "error": "no model selected"}
        chosen = self._load_settings().get("vad")
        chosen = dict(chosen) if isinstance(chosen, dict) else {}
        chosen[name] = bool(on)
        self._save_settings(vad=chosen)
        await Plugin.stop_server(self)  # restarts with the new setting on next use
        return {"ok": True, "vad": bool(on)}

    def _save_selected(self, name: str):
        self._save_settings(model=name)

    async def download_model(self, name: str) -> dict:
        """Download a model. It becomes the current model only when the user selects it."""
        if name not in MODELS:
            return {"ok": False, "error": "unknown model"}
        if name in self.download_progress:
            return {"ok": False, "error": "already downloading"}
        if not self._model_ok(name):
            dest = self._model_path(name)
            tmp = dest + ".part"
            url = ubermoc_models.model_url(name)
            self.cancel_flags.discard(name)
            self.download_progress[name] = {"done": 0, "total": MODELS[name][0] * 1024 * 1024}

            def work():
                have = os.path.getsize(tmp) if os.path.exists(tmp) else 0
                headers = {"User-Agent": "ubermoc-speech"}
                if have:
                    headers["Range"] = f"bytes={have}-"
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, context=_ssl_context(), timeout=60) as r:
                    resumed = r.status == 206
                    if have and not resumed:
                        have = 0
                    total = have + int(r.headers.get("Content-Length") or 0)
                    done = have
                    self.download_progress[name] = {"done": done, "total": total or self.download_progress[name]["total"]}
                    with open(tmp, "ab" if resumed else "wb") as out:
                        while chunk := r.read(1 << 18):
                            if name in self.cancel_flags:
                                raise IOError("cancelled (partial download kept)")
                            out.write(chunk)
                            done += len(chunk)
                            self.download_progress[name]["done"] = done
                if total and os.path.getsize(tmp) != total:
                    raise IOError("download incomplete; select the model again to resume")
                os.replace(tmp, dest)

            try:
                await asyncio.get_event_loop().run_in_executor(None, work)
            except Exception as e:  # noqa: BLE001
                decky.logger.error(f"download failed: {e}")
                self.download_progress.pop(name, None)
                self.cancel_flags.discard(name)
                return {"ok": False, "error": str(e)}
            self.download_progress.pop(name, None)
        if self._model_ok(name):
            return {"ok": True}  # it is only used once the user selects it
        return {"ok": False, "error": "downloaded file looks incomplete"}

    async def delete_model(self, name: str) -> dict:
        if name not in MODELS:
            return {"ok": False, "error": "unknown model"}
        if name in self.download_progress:
            return {"ok": False, "error": "it is still downloading"}
        if name == self._selected() and self.record_proc and self.record_proc.poll() is None:
            return {"ok": False, "error": "stop recording first"}
        was_current = name == self._selected()
        if was_current:
            await Plugin.stop_server(self)  # release the model file
        for path in (self._model_path(name), self._model_path(name) + ".part"):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass
            except OSError as e:
                return {"ok": False, "error": str(e)}
        return {"ok": True, "was_current": was_current, "current": self._selected()}

    async def cancel_download(self, name: str) -> bool:
        self.cancel_flags.add(name)
        return True

    async def get_download_progress(self) -> dict:
        return self.download_progress

    async def status(self) -> dict:
        binary = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", ubermoc_models.binary_name(self._selected() or DEFAULT_MODEL))
        return {
            "binary": os.path.exists(binary),
            "server": bool(self.server_proc and self.server_proc.poll() is None),
            "recording": bool(self.record_proc and self.record_proc.poll() is None),
        }

    # ---------- speech server (whisper.cpp or parakeet.cpp) ----------
    async def start_server(self, name: str = "") -> dict:
        """Start the speech server for `name` (default: the selected model). Used by dictation and by the voice test."""
        name = name or self._selected()
        if self.server_proc and self.server_proc.poll() is None:
            if not name or name == self.server_model:
                return {"ok": True}
            await Plugin.stop_server(self)       # a different model is wanted: restart with it
        if not name:
            return {"ok": False, "error": "no model selected; download one and select it first"}
        bname = ubermoc_models.binary_name(name)
        binary = os.path.join(decky.DECKY_PLUGIN_DIR, "bin", bname)
        if not os.path.exists(binary):
            return {"ok": False, "error": f"{bname} binary missing"}
        os.chmod(binary, 0o755)
        env = {**os.environ, "LD_LIBRARY_PATH": os.path.dirname(binary)}
        vad = os.path.join(os.path.dirname(binary), VAD_MODEL)
        cmd = ubermoc_models.server_command(
            name, binary, self._model_path(name), HOST, PORT, min(4, os.cpu_count() or 4),
            vad_model=vad if os.path.exists(vad) else "", vad_on=self._vad_on(name),
            vad_threshold=Plugin._vad_threshold(name))
        os.makedirs(decky.DECKY_PLUGIN_LOG_DIR, exist_ok=True)
        log = open(os.path.join(decky.DECKY_PLUGIN_LOG_DIR, f"{bname}.log"), "ab")
        self.server_proc = subprocess.Popen(cmd, stdout=log, stderr=log, env=env)
        self.server_model = name
        deadline = time.time() + 90  # model load takes a few seconds
        while time.time() < deadline:
            if self.server_proc.poll() is not None:
                return {"ok": False, "error": f"{bname} exited; see its log"}
            try:
                with socket.create_connection((HOST, PORT), timeout=1):
                    return {"ok": True}
            except OSError:
                await asyncio.sleep(0.25)
        return {"ok": False, "error": f"{bname} did not start in time"}

    async def stop_server(self) -> dict:
        proc = self.server_proc
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        self.server_proc = None
        self.server_task = None
        self.server_model = ""
        return {"ok": True}

    # ---------- recording + transcription ----------
    async def start_recording(self) -> dict:
        """Returns only once audio is actually being captured, so the panel can
        say "speak now" at the right moment."""
        if self.phase == "recording":
            return {"ok": True, "ready_s": 0.0}
        if self.phase != "idle":
            return {"ok": False, "error": "busy"}
        if not self._selected():
            return {"ok": False, "error": "no model selected; download one and select it first"}
        # Ask the Steam interface which app is in front now (it answers while we record).
        try:
            await decky.emit("query_app")
        except Exception:
            pass
        tool = shutil.which("parecord") or shutil.which("pw-record")
        if not tool:
            return {"ok": False, "error": "no parecord/pw-record found"}
        self.phase = "starting"
        path = f"/tmp/ubermoc-speech-{uuid.uuid4().hex}.wav"
        if os.path.basename(tool) == "parecord":
            cmd = [tool, "--file-format=wav", "--rate=16000", "--channels=1", "--latency-msec=20", path]
        else:
            cmd = [tool, "--rate", "16000", "--channels", "1", "--latency", "20ms", path]
        runtime = f"/run/user/{self._user_uid()}"
        env = {**os.environ, "XDG_RUNTIME_DIR": runtime, "PULSE_SERVER": f"unix:{runtime}/pulse/native"}
        t0 = time.time()
        try:
            proc = subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self.phase = "idle"
            return {"ok": False, "error": f"could not start the recorder: {e}"}
        self.record_proc, self.record_path = proc, path

        # Load the model while the user talks, so it is ready when they stop.
        if not (self.server_proc and self.server_proc.poll() is None):
            self.server_task = asyncio.ensure_future(Plugin.start_server(self))

        # Wait until the WAV file starts growing past its 44-byte header.
        while time.time() - t0 < 5:
            if proc.poll() is not None:
                self.record_proc = None
                self.phase = "idle"
                return {"ok": False, "error": "the microphone recorder stopped; is a microphone available?"}
            if os.path.exists(path) and os.path.getsize(path) > 64:
                break
            await asyncio.sleep(0.02)
        self.phase = "recording"
        Plugin._buzz(self, "start")
        return {"ok": True, "ready_s": round(time.time() - t0, 2)}

    def _rumble_level(self) -> int:
        return ubermoc_rumble.level_of(self._load_settings().get("rumble_level"))

    def _buzz(self, which: str):
        """Feel that recording started / stopped (every controller that can rumble; can be turned off)."""
        if self._load_settings().get("rumble") is False:
            return
        if self.rumbler is None:
            self.rumbler = ubermoc_rumble.Rumbler()
        try:
            self.rumbler.play(which, self._rumble_level())
        except Exception as e:  # noqa: BLE001 - a missing buzz must never break dictation
            decky.logger.warning(f"rumble failed: {e}")

    async def get_rumble(self) -> dict:
        return {"on": self._load_settings().get("rumble") is not False, "level": self._rumble_level()}

    async def set_rumble(self, on: bool) -> dict:
        self._save_settings(rumble=bool(on))
        if on:
            Plugin._buzz(self, "start")      # a sample, so the toggle can be felt
        return {"ok": True, "on": bool(on), "level": self._rumble_level()}

    async def set_rumble_level(self, level: int) -> dict:
        level = ubermoc_rumble.level_of(level)
        self._save_settings(rumble_level=level)
        Plugin._buzz(self, "start")          # a sample at the new strength
        return {"ok": True, "level": level}

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
        if self.phase != "recording":
            return {"ok": False, "error": "not recording"}
        self.phase = "transcribing"
        by_activator = self.by_activator
        typed = None
        try:
            result = await Plugin._finish_recording(self)
            if by_activator and result.get("ok") and result.get("text"):
                self.phase = "typing"
                typed = await Plugin._deliver_text(self, result["text"])
        finally:
            self.phase = "idle"
            self.by_activator = False
        if typed is not None:
            result = {**result, "typed": typed}
        # A quick accidental tap of the activator shouldn't erase the last text.
        if not (by_activator and result.get("error") == "no audio captured"):
            self.last_result = {**result, "at": time.time(), "by": "activator" if by_activator else "panel"}
        return result

    async def _finish_recording(self) -> dict:
        path = self.record_path
        self._stop_recorder()
        Plugin._buzz(self, "stop")       # after the recorder ends, so the buzz is not recorded
        if not path or not os.path.exists(path) or os.path.getsize(path) < 2000:
            return {"ok": False, "error": "no audio captured"}
        t0 = time.time()
        try:
            started = await (self.server_task or Plugin.start_server(self))
            if not started.get("ok"):
                return started
            t1 = time.time()
            text = await asyncio.get_event_loop().run_in_executor(None, self._transcribe, path)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"transcribe failed: {e}")
            return {"ok": False, "error": str(e)}
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        t2 = time.time()
        return {"ok": True, "text": text, "wait_s": round(t1 - t0, 2), "transcribe_s": round(t2 - t1, 2)}

    async def get_state(self) -> dict:
        """Polled by the panel: what the dictation is doing and what it last heard."""
        self.panel_seen = time.time()          # the panel is open: results are shown there, not typed
        return {"phase": self.phase, "last": self.last_result,
                "activator_held": bool(self.hub and self.hub.active)}

    # ---------- typing the text ----------
    async def _deliver_text(self, text: str, force: bool = False) -> dict:
        """Press the "before" key, type `text`, press the "after" key, into whatever
        has focus. Returns what happened, for the panel."""
        s = self._load_settings()
        if not force:
            if time.time() - self.panel_seen < 1.5:
                return {"status": "panel"}      # the menu is open: show the text there instead
        cleaned = ubermoc_typing.clean_for_typing(text)
        if cleaned and ubermoc_typing.symbols_for(s, self._running_appid()) == "symbols":
            cleaned = ubermoc_typing.spoken_symbols(cleaned)
        if not cleaned:
            return {"status": "empty"}
        # Wait for the activator's own keys to be released, so a still-held Ctrl or
        # Shift cannot turn the typing into shortcuts.
        tokens = self.hub.activator if self.hub else []
        deadline = time.time() + 2.0
        while self.hub and tokens and self.hub.any_held(tokens) and time.time() < deadline:
            await asyncio.sleep(0.02)
        speed = s.get("type_speed") if s.get("type_speed") in ubermoc_typing.TIMING else "normal"
        delay, hold, settle_before, settle_after = ubermoc_typing.TIMING[speed]
        before, after, app_name = ubermoc_typing.actions_for(s, self._running_appid())
        notes = []
        pressed_before = pressed_after = None

        if ubermoc_typing.action_tokens(before):
            err = await Plugin._press(self, ubermoc_typing.action_tokens(before), hold)
            if err:
                notes.append(f"The input before the text was not pressed: {err}")
            else:
                pressed_before = ubermoc_typing.action_label(before)
                await asyncio.sleep(settle_before)      # give a chat box time to open

        result = None
        if s.get("type_method") == "steam":
            if await Plugin._type_via_steam(self, cleaned):
                result = {"status": "typed", "method": "steam", "typed": len(cleaned), "skipped": 0}
            else:
                notes.append("The Steam keyboard did not answer, so the virtual keyboard was used.")
        if result is None:
            try:
                r = await asyncio.get_event_loop().run_in_executor(None, self._type_blocking, cleaned, delay)
                result = {"status": "typed", "method": "keyboard", **r}
            except ubermoc_typing.KeyboardError as e:
                decky.logger.error(f"typing failed: {e}")
                return {"status": "error", "error": str(e), "before": pressed_before}

        if ubermoc_typing.action_tokens(after):
            await asyncio.sleep(settle_after)
            err = await Plugin._press(self, ubermoc_typing.action_tokens(after), hold)
            if err:
                notes.append(f"The input after the text was not pressed: {err}")
            else:
                pressed_after = ubermoc_typing.action_label(after)
        return {**result, "before": pressed_before, "after": pressed_after, "app": app_name,
                "note": " ".join(notes)}

    def _get_presser(self):
        if self.keyboard is None:
            self.keyboard = ubermoc_typing.VirtualKeyboard()
        if self.presser is None:
            self.presser = ubermoc_typing.Presser(self.keyboard)
        return self.presser

    async def _press(self, tokens: list, hold: float) -> str:
        """Press keys / mouse buttons / controller buttons on the virtual devices.
        Returns "" or an error text."""
        def work():
            return self._get_presser().press(tokens, hold)

        try:
            r = await asyncio.get_event_loop().run_in_executor(None, work)
            if r["skipped"] and not r["pressed"]:
                return f"{', '.join(r['skipped'])} cannot be pressed on a virtual device"
            return ""
        except ubermoc_typing.KeyboardError as e:
            decky.logger.error(f"input press failed: {e}")
            return str(e)

    def _all_action_tokens(self) -> list:
        """Inputs of the global before/after and of every override."""
        s = self._load_settings()
        tokens = []
        actions = [s.get("before", ubermoc_typing.DEFAULT_ACTION), s.get("after", ubermoc_typing.DEFAULT_ACTION)]
        for ov in ubermoc_typing.normalize_overrides(s.get("overrides")).values():
            actions += [ov["before"], ov["after"]]
        for action in actions:
            tokens += ubermoc_typing.action_tokens(action)
        return tokens

    async def _prepare_outputs_if_custom(self):
        """At startup, only bother when a before/after input needs a mouse or gamepad."""
        if any(not t.startswith("key:") for t in self._all_action_tokens()):
            await Plugin._prepare_outputs(self)

    async def _prepare_outputs(self):
        """Create the virtual devices the chosen before/after inputs need, ahead of
        time, so the first press is not lost while the desktop notices a new device."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, self._get_presser().prepare, self._all_action_tokens())
        except ubermoc_typing.KeyboardError as e:
            decky.logger.warning(f"could not prepare virtual devices: {e}")

    def _type_blocking(self, text: str, delay: float) -> dict:
        if self.keyboard is None:
            self.keyboard = ubermoc_typing.VirtualKeyboard()
        return self.keyboard.type_text(text, delay)

    async def _type_via_steam(self, text: str) -> bool:
        """Gaming mode only: ask the Steam interface (via the panel's frontend) to type."""
        msg_id = uuid.uuid4().hex
        fut = asyncio.get_event_loop().create_future()
        self.type_acks[msg_id] = fut
        try:
            await decky.emit("type_text", {"id": msg_id, "text": text})
            return bool(await asyncio.wait_for(fut, 1.0))
        except Exception:  # noqa: BLE001  (no frontend answered: e.g. desktop mode)
            return False
        finally:
            self.type_acks.pop(msg_id, None)

    async def type_ack(self, msg_id: str, ok: bool) -> bool:
        fut = self.type_acks.get(msg_id)
        if fut and not fut.done():
            fut.set_result(bool(ok))
        return True

    def _action(self, which: str) -> dict:
        action = ubermoc_typing.normalize_action(self._load_settings().get(which))
        return {**action, "label": ubermoc_typing.action_label(action),
                "warnings": ubermoc_typing.action_warnings(action)}

    async def get_typing(self) -> dict:
        s = self._load_settings()
        available, reason = ubermoc_typing.VirtualKeyboard().check()
        return {
            "symbols": "symbols" if s.get("type_symbols") == "symbols" else "words",
            "method": s.get("type_method") if s.get("type_method") in ("keyboard", "steam") else "keyboard",
            "speed": s.get("type_speed") if s.get("type_speed") in ubermoc_typing.TIMING else "normal",
            "keyboard": {"available": available, "reason": reason},
            "before": self._action("before"),
            "after": self._action("after"),
            "test": self.last_typing_test,
        }

    async def set_typing(self, symbols: str, method: str, speed: str) -> dict:
        self._save_settings(
            type_symbols="symbols" if symbols == "symbols" else "words",
            type_method=method if method in ("keyboard", "steam") else "keyboard",
            type_speed=speed if speed in ubermoc_typing.TIMING else "normal",
        )
        return await Plugin.get_typing(self)

    # ---------- what is running, and per-application overrides ----------
    async def set_running_app(self, appid: str, name: str) -> bool:
        """The panel's frontend reports the running Steam game / non-Steam app."""
        appid = str(appid or "")
        self.running_app = {"appid": appid if appid.isdigit() and appid != "0" else "",
                            "name": str(name or ""), "t": time.time()}
        return True

    def _running_appid(self) -> str:
        r = self.running_app
        return r["appid"] if r else ""

    def _action_info(self, action: dict) -> dict:
        return {**action, "label": ubermoc_typing.action_label(action),
                "warnings": ubermoc_typing.action_warnings(action)}

    async def get_overrides(self) -> dict:
        overrides = ubermoc_typing.normalize_overrides(self._load_settings().get("overrides"))
        running = self._running_appid()
        glob_symbols = "symbols" if self._load_settings().get("type_symbols") == "symbols" else "words"
        return {
            "overrides": [
                {"appid": appid, "name": ov["name"], "kind": ov["kind"],
                 "before": self._action_info(ov["before"]), "after": self._action_info(ov["after"]),
                 "symbols": ov.get("symbols") or glob_symbols}
                for appid, ov in sorted(overrides.items(), key=lambda kv: kv[1]["name"].lower())
            ],
            "running": {"appid": running, "name": (self.running_app or {}).get("name", "") if running else "",
                        "has_override": running in overrides},
        }

    async def add_override(self, appid: str, name: str, kind: str) -> dict:
        """Give a game / app its own before/after. It starts as a copy of the global
        options, so nothing changes until you change it."""
        appid = str(appid)
        s = self._load_settings()
        overrides = ubermoc_typing.normalize_overrides(s.get("overrides"))
        if appid.isdigit() and name and appid not in overrides and len(overrides) < ubermoc_typing.MAX_OVERRIDES:
            overrides[appid] = {
                "name": name, "kind": "shortcut" if kind == "shortcut" else "steam",
                "before": ubermoc_typing.normalize_action(s.get("before")),
                "after": ubermoc_typing.normalize_action(s.get("after")),
                "symbols": "symbols" if s.get("type_symbols") == "symbols" else "words",
            }
            self._save_settings(overrides=ubermoc_typing.normalize_overrides(overrides))
        return await Plugin.get_overrides(self)

    async def set_override_symbols(self, appid: str, symbols: str) -> dict:
        overrides = ubermoc_typing.normalize_overrides(self._load_settings().get("overrides"))
        if str(appid) in overrides:
            overrides[str(appid)]["symbols"] = "symbols" if symbols == "symbols" else "words"
            self._save_settings(overrides=overrides)
        return await Plugin.get_overrides(self)

    async def remove_override(self, appid: str) -> dict:
        overrides = ubermoc_typing.normalize_overrides(self._load_settings().get("overrides"))
        if overrides.pop(str(appid), None) is not None:
            self._save_settings(overrides=overrides)
        return await Plugin.get_overrides(self)

    def _save_override_action(self, appid: str, which: str, action: dict) -> bool:
        overrides = ubermoc_typing.normalize_overrides(self._load_settings().get("overrides"))
        if appid not in overrides or which not in ("before", "after"):
            return False
        overrides[appid][which] = ubermoc_typing.normalize_action(action)
        self._save_settings(overrides=overrides)
        asyncio.ensure_future(Plugin._prepare_outputs(self))
        return True

    # ---------- keys / inputs pressed before and after the text ----------
    async def set_key_action(self, which: str, mode: str, keys: list, appid: str = "") -> dict:
        """Choose what is pressed before/after the typed text: None, Enter, or any
        recorded key / mouse button / controller button (or a combination). With an
        appid it is that application's override; otherwise the global option."""
        action = {"mode": mode, "keys": keys}
        if appid:
            self._save_override_action(str(appid), which, action)
            return await Plugin.get_overrides(self)
        if which in ("before", "after"):
            self._save_settings(**{which: ubermoc_typing.normalize_action(action)})
            asyncio.ensure_future(Plugin._prepare_outputs(self))
        return await Plugin.get_typing(self)

    async def start_key_capture(self, which: str, appid: str = "") -> dict:
        """Listen for what the user holds together and then lets go of: a keyboard,
        a mouse or a controller, exactly like recording the activator."""
        if which not in ("before", "after") or not self.hub:
            return {"ok": False, "error": "input is not available"}
        appid = str(appid or "")
        self.hub.begin_capture(
            timeout=15.0, purpose=f"{which}@{appid}",
            on_done=lambda toks, w=which, a=appid: Plugin._key_captured(self, w, a, toks))
        return {"ok": True}

    def _key_captured(self, which: str, appid: str, tokens: list):
        if not tokens:
            return
        action = {"mode": "custom", "keys": tokens}
        if appid:
            self._save_override_action(appid, which, action)
        else:
            self._save_settings(**{which: ubermoc_typing.normalize_action(action)})
            asyncio.ensure_future(Plugin._prepare_outputs(self))

    async def type_test(self, delay_s: float = 5.0) -> dict:
        """Type a short sentence after a delay (so the user can click a text box first)."""
        self.last_typing_test = None

        async def run():
            await asyncio.sleep(max(0.0, min(float(delay_s), 15.0)))
            r = await Plugin._deliver_text(self, TYPE_TEST_TEXT, force=True)
            self.last_typing_test = {**r, "at": time.time()}

        asyncio.ensure_future(run())
        return {"ok": True}

    # ---------- activator (hold to talk) ----------
    def _on_activator(self, active: bool):
        """Called by the input hub, on the event loop, when the activator is
        pressed (True) or released (False)."""
        self.act_down = active
        asyncio.ensure_future(Plugin._act_press(self) if active else Plugin._act_release(self))

    async def _act_press(self):
        if self.phase != "idle":
            return
        self.by_activator = True
        r = await Plugin.start_recording(self)
        if not r.get("ok"):
            self.by_activator = False
            self.last_result = {"ok": False, "error": r.get("error", "could not start"),
                                "at": time.time(), "by": "activator"}
            return
        if not self.act_down:           # let go while the microphone was still starting: discard
            await Plugin._discard_recording(self)

    async def _discard_recording(self):
        path = self.record_path
        self._stop_recorder()
        Plugin._buzz(self, "stop")
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        self.phase = "idle"
        self.by_activator = False

    async def _act_release(self):
        if self.phase == "recording" and self.by_activator:
            await Plugin.stop_and_transcribe(self)

    def _tokens(self) -> list:
        return ubermoc_input.valid_tokens(self._load_settings().get("activator"))

    async def get_activator(self) -> dict:
        tokens = self._tokens()
        return {
            "tokens": tokens,
            "label": ubermoc_input.describe(tokens) if tokens else "",
            "warnings": ubermoc_input.warnings_for(tokens),
            "watching": self.hub.status() if self.hub else {},
        }

    async def set_ui_pad(self, tokens: list) -> bool:
        """Controller buttons held according to the Steam interface (sent by the panel)."""
        if self.hub:
            self.hub.set_ui_held(tokens)
        return True

    async def start_capture(self) -> dict:
        """Listen for the next combination the user holds and releases."""
        if not self.hub:
            return {"ok": False, "error": "input is not available"}
        self.hub.begin_capture(timeout=15.0, on_done=lambda toks: Plugin._captured(self, toks))
        return {"ok": True}

    def _captured(self, tokens: list):
        if tokens:
            self._save_settings(activator=tokens)
            self.hub.set_activator(tokens)

    async def capture_status(self) -> dict:
        st = self.hub.capture_status() if self.hub else {"state": "idle", "tokens": [], "label": ""}
        st["warnings"] = (ubermoc_input.warnings_for(st["tokens"])
                          if st["state"] == "done" and st.get("purpose") == "activator" else [])
        return st

    async def cancel_capture(self) -> bool:
        if self.hub:
            self.hub.cancel_capture()
        return True

    async def clear_activator(self) -> dict:
        self._save_settings(activator=[])
        if self.hub:
            self.hub.set_activator([])
        return {"ok": True}

    def _transcribe(self, path: str, name: str = "") -> str:
        seconds = max(0.0, (os.path.getsize(path) - 44) / WAV_BYTES_PER_SECOND)
        url_path, fields = ubermoc_models.transcribe_request(name or self._selected(), seconds, SHORT_AUDIO_CONTEXT)
        boundary = uuid.uuid4().hex
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
            f"http://{HOST}:{PORT}{url_path}", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode()).get("text", "").strip()

    # ---------- voice test ----------
    # A guided test: the person reads 22 phrases, each is recorded (it stops by itself), every chosen model transcribes them,
    # and the scores say which model is best for THIS voice on THIS device. Nothing here needs an AI model: the correct text is
    # known because the person reads it. Recordings live in a temporary folder and are deleted as soon as the scoring is done.
    vt: dict | None = None             # the test being run
    vt_level: dict = {"db": -90.0, "speaking": False}
    vt_run: dict | None = None         # progress of the scoring run
    vt_cancel_flag: bool = False

    def _vt_env(self) -> dict:
        runtime = f"/run/user/{self._user_uid()}"
        return {**os.environ, "XDG_RUNTIME_DIR": runtime, "PULSE_SERVER": f"unix:{runtime}/pulse/native"}

    def _vt_level(self, rms: float, speaking: bool):
        self.vt_level = {"db": round(ubermoc_audio.db(rms), 1), "speaking": bool(speaking)}

    def _vt_cleanup(self):
        info, self.vt = self.vt, None
        if info and info.get("dir"):
            shutil.rmtree(info["dir"], ignore_errors=True)
        if self.phase == "voicetest":
            self.phase = "idle"
        self.vt_level = {"db": -90.0, "speaking": False}

    async def vt_plan(self, names: list) -> dict:
        """What the setup screen shows: the phrase count, the device, free space and a warning if the downloads are large."""
        sel = {n: MODELS[n][0] for n in names if n in MODELS}
        installed = {n for n in MODELS if self._model_ok(n)}
        handheld = ubermoc_voicetest.is_handheld()
        free = ubermoc_voicetest.free_mb(self._models_dir())
        return {"phrases": len(ubermoc_voicetest.PHRASES), "minutes": 5, "handheld": handheld, "free_mb": free,
                "plan": ubermoc_voicetest.space_plan(sel, installed, free, handheld),
                "recorder": bool(ubermoc_audio.recorder_tool()), "saved": self._load_settings().get("voicetest") or {}}

    async def vt_begin(self, names: list, condition: str) -> dict:
        """Start a test with these (downloaded) models. condition: "noisy" (the default, how people really play) or "quiet"."""
        if self.vt:
            Plugin._vt_cleanup(self)
        if self.phase != "idle":
            return {"ok": False, "error": "busy: finish dictation first"}
        names = [n for n in names if n in MODELS]
        if not names:
            return {"ok": False, "error": "pick at least one model"}
        missing = [n for n in names if not self._model_ok(n)]
        if missing:
            return {"ok": False, "error": "download these first: " + ", ".join(missing)}
        if not ubermoc_audio.recorder_tool():
            return {"ok": False, "error": "no recorder program (parec/parecord) was found"}
        self.vt = {"dir": tempfile.mkdtemp(prefix="ubermoc-voicetest-"), "models": names, "condition": "quiet" if condition == "quiet" else "noisy", "clips": {}}
        self.vt_cancel_flag = False
        self.vt_run = None
        self.phase = "voicetest"
        return {"ok": True, "total": len(ubermoc_voicetest.PHRASES)}

    async def vt_mic_check(self) -> dict:
        """Listen for five seconds while the person says "testing one two three"; report the level in plain words."""
        if self.phase not in ("idle", "voicetest"):
            return {"ok": False, "error": "busy"}
        was_idle = self.phase == "idle"
        self.phase = "voicetest"
        try:
            env = self._vt_env()
            peak, level = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ubermoc_audio.level_check(lambda: ubermoc_audio.stream(env), 5.0, self._vt_level))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        finally:
            if was_idle:
                self.phase = "idle"
        kind, message = ubermoc_audio.mic_verdict(peak)
        return {"ok": True, "peak": peak, "level": round(level), "kind": kind, "message": message}

    async def vt_phrase(self, i: int) -> dict:
        ph = ubermoc_voicetest.PHRASES
        if not 0 <= i < len(ph):
            return {"ok": False, "error": "no such phrase"}
        kind, text, _accept, hint = ph[i]
        return {"ok": True, "i": i, "total": len(ph), "kind": kind, "text": text, "hint": hint}

    async def vt_record(self, i: int) -> dict:
        """Record phrase i. It starts listening at once and stops by itself about a second after the person stops talking."""
        if not self.vt or not 0 <= i < len(ubermoc_voicetest.PHRASES):
            return {"ok": False, "error": "no test is running"}
        path = os.path.join(self.vt["dir"], f"{i:02d}.wav")
        env = self._vt_env()
        self.vt_cancel_flag = False
        try:
            reason, sec = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ubermoc_audio.record_auto(path, lambda: ubermoc_audio.stream(env), self._vt_level, lambda: self.vt_cancel_flag))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        _s, peak = ubermoc_audio.wav_stats(path)
        warn = ("" if reason == "finished" and peak >= 3000 else
                "Nothing was heard. Is the microphone on and not muted?" if reason == "nothing-heard" else
                "It kept listening for 20 seconds. Try again." if reason == "too-long" else
                "Very quiet. Speak up or move the microphone closer." if reason == "finished" else "")
        if reason == "finished" and peak >= 32000:
            warn = "The sound is clipping. Move the microphone a little farther away."
        ok = reason == "finished" and sec >= 0.8 and peak >= 800
        if ok:
            self.vt["clips"][i] = path
        return {"ok": ok, "reason": reason, "seconds": round(sec, 1), "peak": peak, "warn": warn}

    async def vt_status(self) -> dict:
        v = self.vt
        return {"active": bool(v), "level": self.vt_level, "recorded": sorted(v["clips"]) if v else [], "run": self.vt_run,
                "condition": v["condition"] if v else "", "phase": self.phase}

    async def vt_run_start(self) -> dict:
        """Run every chosen model over the recordings, one at a time. Progress is in vt_status()."""
        v = self.vt
        if not v or not v["clips"]:
            return {"ok": False, "error": "record some phrases first"}
        if self.vt_run and not self.vt_run.get("done"):
            return {"ok": False, "error": "already running"}
        self.vt_run = {"model": "", "index": 0, "total": len(v["models"]), "clip": 0, "clips": len(v["clips"]), "done": False, "error": "", "results": None}
        asyncio.ensure_future(Plugin._vt_run_task(self))
        return {"ok": True}

    async def _vt_run_task(self):
        v, run = self.vt, self.vt_run
        results = {}
        try:
            for mi, name in enumerate(v["models"]):
                if self.vt_cancel_flag:
                    break
                run.update(model=name, index=mi, clip=0)
                await Plugin.stop_server(self)
                started = await Plugin.start_server(self, name)
                if not started.get("ok"):
                    results[name] = {"score": None, "error": started.get("error", "could not start")}
                    continue
                rows = []
                for i, path in sorted(v["clips"].items()):
                    if self.vt_cancel_flag:
                        break
                    t0 = time.time()
                    try:
                        heard = await asyncio.get_event_loop().run_in_executor(None, self._transcribe, path, name)
                    except Exception as e:  # noqa: BLE001
                        heard = ""
                        decky.logger.warning(f"voice test: {name} failed on phrase {i}: {e}")
                    sec = time.time() - t0
                    group, errors, units, _subs = ubermoc_voicetest.score_phrase(ubermoc_voicetest.PHRASES[i], heard)
                    rows.append({"group": group, "errors": errors, "units": units, "sec": sec})
                    run["clip"] = len(rows)
                summary = ubermoc_voicetest.summarize(rows)
                summary["memory_mb"] = ubermoc_voicetest.process_memory_mb(self.server_proc.pid) if self.server_proc else 0
                summary["size_mb"] = MODELS[name][0]
                results[name] = summary
                await Plugin.stop_server(self)
            if not self.vt_cancel_flag:
                verdict = ubermoc_voicetest.verdict(results)
                out = {"models": results, "verdict": verdict, "when": int(time.time()), "phrases": len(v["clips"])}
                run["results"] = out
                saved = dict(self._load_settings().get("voicetest") or {})
                saved[v["condition"]] = out
                self._save_settings(voicetest=saved)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"voice test failed: {e}")
            run["error"] = str(e)
        finally:
            await Plugin.stop_server(self)
            shutil.rmtree(v["dir"], ignore_errors=True)      # the recordings are deleted as soon as they are scored
            v["clips"] = {}
            run["done"] = True
            if self.phase == "voicetest":
                self.phase = "idle"

    async def vt_cancel(self) -> dict:
        self.vt_cancel_flag = True
        if not (self.vt_run and not self.vt_run.get("done")):
            Plugin._vt_cleanup(self)
        return {"ok": True}

    async def vt_finish(self) -> dict:
        """Leave the test screen: delete any recordings and make dictation available again."""
        if self.vt_run and not self.vt_run.get("done"):
            self.vt_cancel_flag = True
            return {"ok": True}
        Plugin._vt_cleanup(self)
        self.vt_run = None
        return {"ok": True}

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

    def _stage_update(self) -> tuple[str, str]:
        """Download the update, check it and unpack it. Nothing is changed until it is good."""
        rel = self._fetch_release()
        asset = next((a for a in rel.get("assets", []) if a["name"].endswith(".zip")), None)
        if asset is None:
            raise IOError("the latest release has no plugin zip")
        latest = self._parse_release(rel)["version"]
        work = tempfile.mkdtemp(prefix="ubermoc-update-")
        zpath = os.path.join(work, "update.zip")
        req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "ubermoc-speech"})
        with urllib.request.urlopen(req, context=_ssl_context(), timeout=120) as r, open(zpath, "wb") as out:
            shutil.copyfileobj(r, out)
        if os.path.getsize(zpath) != asset["size"]:
            raise IOError("update download incomplete")
        extract = os.path.join(work, "x")
        try:
            with zipfile.ZipFile(zpath) as z:
                if z.testzip() is not None:
                    raise IOError("update zip is corrupt")
                names = set(z.namelist())
                plugin_json = z.read(f"{PLUGIN_NAME}/plugin.json")
                root = os.path.realpath(extract)
                for name in names:  # refuse path traversal
                    if ".." in name.split("/") or name.startswith("/") or not os.path.realpath(
                        os.path.join(root, name)
                    ).startswith(root + os.sep):
                        raise IOError(f"unsafe path in update zip: {name}")
                for need in PLUGIN_FILES:
                    if f"{PLUGIN_NAME}/{need}" not in names:
                        raise IOError(f"update is missing {need}")
                try:
                    name = json.loads(plugin_json).get("name")
                except ValueError:
                    name = None
                if name != PLUGIN_NAME:
                    raise IOError("the update is for a different plugin")
                z.extractall(extract)
        except (zipfile.BadZipFile, KeyError) as e:
            raise IOError(f"the update isn't a usable plugin zip: {e}") from e
        return os.path.join(extract, PLUGIN_NAME), latest

    def _apply(self, top: str):
        """Overwrite plugin files; every file goes via a temp name + atomic rename."""
        dest_root = decky.DECKY_PLUGIN_DIR
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
        loop = asyncio.get_event_loop()
        try:
            top, latest = await loop.run_in_executor(None, self._stage_update)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"update download failed: {e}")
            return {"ok": False, "error": str(e)}
        try:
            await Plugin.stop_server(self)
            await loop.run_in_executor(None, self._apply, top)
        except Exception as e:  # noqa: BLE001
            decky.logger.error(f"update apply failed: {e}")
            return {"ok": False, "error": str(e)}
        # The panel now asks Decky to reload the plugin, which restarts this backend
        # and loads the new panel.
        decky.logger.info(f"installed update {latest}")
        return {"ok": True, "version": latest}
