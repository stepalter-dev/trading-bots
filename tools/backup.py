"""Refresh the off-GitHub backup in Google Drive (the third copy, after GitHub and this folder).

    python tools/backup.py

Pulls the latest commits (the bots push data all day), then writes:
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEST = r"G:\My Drive\JAW Digital (1)\investment-bot\trading-bots-backup"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def main():
    git("pull", "-q", "--rebase", "origin", "main")
    os.makedirs(DEST, exist_ok=True)
    bundle = os.path.join(DEST, "trading-bots.bundle")
    tmp = bundle + ".new"
    git("bundle", "create", tmp, "--all")
    git("bundle", "verify", tmp)
    os.replace(tmp, bundle)

    files = os.path.join(DEST, "files")
    shutil.rmtree(files, ignore_errors=True)
    with tempfile.TemporaryDirectory() as td:
        tar = os.path.join(td, "head.tar")
        git("archive", "-o", tar, "HEAD")
        with tarfile.open(tar) as t:
            t.extractall(files)

    head = git("rev-parse", "--short", "HEAD")
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with open(os.path.join(DEST, "README.txt"), "w", encoding="utf-8") as f:
        f.write(f"Backup of github.com/stepalter-dev/trading-bots  (last updated {now}, commit {head})\n\n"
                "trading-bots.bundle  = the COMPLETE git repository with full history, in one file.\n"
                "                       Restore anywhere with:   git clone trading-bots.bundle trading-bots\n"
                "files/               = a plain copy of every file at that commit (code, dashboard, data).\n\n"
                "To refresh this backup, run in C:\\Users\\Winds\\trading-bots:   python tools/backup.py\n")
    print(f"backup refreshed at commit {head} -> {DEST}")


if __name__ == "__main__":
    main()
