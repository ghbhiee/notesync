"""Git engine with external GIT_DIR (DESIGN.md #6): zero cache files in SYNC."""
import os
import subprocess
from pathlib import Path


class GitStore:
    def __init__(self, git_dir, work_tree):
        self.git_dir = Path(git_dir)
        self.work_tree = Path(work_tree)
        self.env = {**os.environ,
                    "GIT_DIR": str(self.git_dir),
                    "GIT_WORK_TREE": str(self.work_tree)}

    def _git(self, *args, check=True):
        r = subprocess.run(["git", *args], env=self.env, cwd=str(self.work_tree),
                           capture_output=True, encoding="utf-8", check=False)
        if check and r.returncode != 0:
            raise RuntimeError(f"git {args[0]}: {r.stderr.strip()[:300]}")
        return r

    def ensure(self):
        self.work_tree.mkdir(parents=True, exist_ok=True)
        if not (self.git_dir / "HEAD").exists():
            self.git_dir.parent.mkdir(parents=True, exist_ok=True)
            self._git("init", "-q")
            self._git("config", "user.name", "NoteSync")
            self._git("config", "user.email", "notesync@local")
            self._git("config", "core.quotepath", "false")

    def commit_all(self, msg: str):
        """Stage everything; commit if dirty. -> sha or None if nothing changed."""
        self._git("add", "-A")
        if self._git("diff", "--cached", "--quiet", check=False).returncode == 0:
            return None
        self._git("commit", "-q", "-m", msg)
        return self._git("rev-parse", "HEAD").stdout.strip()

    def show(self, commit: str, rel_path: str):
        """File content at a commit, or None if absent there."""
        r = self._git("show", f"{commit}:{rel_path}", check=False)
        return r.stdout if r.returncode == 0 else None
