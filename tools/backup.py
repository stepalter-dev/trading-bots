"""Refresh the Google Drive backup of github.com/stepalter-dev/trading-bots.

    python backup.py        (works from anywhere - no local copy of the repo needed)

Clones the latest repository from GitHub into a temporary folder, then writes to Google Drive:
- trading-bots.bundle : the whole git repository with full history in one file
                        (restore with: git clone trading-bots.bundle trading-bots)
- files/              : a plain copy of every file at the latest commit
"""
import datetime
import os
import shutil
import subprocess
import tarfile
import tempfile

REPO = "https://github.com/stepalter-dev/trading-bots.git"
DEST = r"G:\My Drive\JAW Digital (1)\investment-bot\trading-bots-backup"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def main():
    os.makedirs(DEST, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        repo = os.path.join(td, "repo")
        git(td, "clone", "-q", "--mirror", REPO, repo)
        bundle = os.path.join(DEST, "trading-bots.bundle")
        tmp = bundle + ".new"
        git(repo, "bundle", "create", tmp, "--all")
        git(repo, "bundle", "verify", tmp)
        os.replace(tmp, bundle)

        files = os.path.join(DEST, "files")
        shutil.rmtree(files, ignore_errors=True)
        tar = os.path.join(td, "head.tar")
        git(repo, "archive", "-o", tar, "HEAD")
        with tarfile.open(tar) as t:
            t.extractall(files, filter="data")
        head = git(repo, "rev-parse", "--short", "HEAD")

    target = os.path.join(DEST, "backup.py")
    if os.path.normcase(os.path.abspath(__file__)) != os.path.normcase(target):
        shutil.copy2(os.path.abspath(__file__), target)  # keep the script next to the backup
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(os.path.join(DEST, "README.txt"), "w", encoding="utf-8") as f:
        f.write(f"Backup of github.com/stepalter-dev/trading-bots  (last updated {now}, commit {head})\n\n"
                "trading-bots.bundle  = the COMPLETE git repository with full history, in one file.\n"
                "                       Restore anywhere with:   git clone trading-bots.bundle trading-bots\n"
                "files/               = a plain copy of every file at that commit (code, dashboard, data).\n"
                "artifact-export/     = the original export of the old claude.ai artifact database (pre-GitHub).\n\n"
                "To refresh this backup, run from this folder:   python backup.py\n")
    print(f"backup refreshed at commit {head} -> {DEST}")


if __name__ == "__main__":
    main()
