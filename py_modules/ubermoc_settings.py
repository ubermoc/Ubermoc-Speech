"""Settings storage that survives restarts, mode switches and odd shutdowns.

* The same settings are written to every path given (Decky's recommended settings
  folder and a copy next to the models), so losing or relocating one still leaves
  the other.
* Every write is atomic (temp file + rename) and forced to disk (fsync of the file
  and its folder). A plain rename without fsync can leave an empty file after the
  machine is stopped abruptly.
* Loading merges all copies: for each setting the most recently written copy wins,
  older copies fill any gaps.
* A file that cannot be parsed is kept as <name>.corrupt instead of being silently
  overwritten.
"""
import json
import os


class SettingsStore:
    def __init__(self, paths, log=None):
        self.paths = list(paths)
        self._log = log or (lambda msg: None)

    # ----- loading -----
    def _read(self, path):
        try:
            with open(path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as e:
            self._log(f"settings file unreadable ({type(e).__name__}): {path}")
            try:
                os.replace(path, path + ".corrupt")
            except OSError:
                pass
            return None
        return data if isinstance(data, dict) else None

    def load(self) -> dict:
        found = []
        for path in self.paths:
            data = self._read(path)
            if data is not None:
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    mtime = 0
                found.append((mtime, data))
        merged = {}
        for _mtime, data in sorted(found, key=lambda t: t[0], reverse=True):   # newest first
            for key, value in data.items():
                merged.setdefault(key, value)
        return merged

    # ----- saving -----
    @staticmethod
    def _write(path, data):
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            dfd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass

    def save(self, **patch) -> bool:
        """Merge `patch` into the stored settings and write every copy.
        Returns True if at least one copy was written."""
        data = self.load()
        data.update(patch)
        written = 0
        for path in self.paths:
            try:
                self._write(path, data)
                written += 1
            except OSError as e:
                self._log(f"could not write settings to {path}: {e}")
        if written == 0:
            self._log("settings were NOT saved anywhere")
        return written > 0
