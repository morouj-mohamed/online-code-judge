#!/usr/bin/env python3
"""
tools/backup.py - back up and restore everything the judge stores.

One backup is ONE .tar.gz file with:
  judge.sqlite3   accounts, submissions, results, contests, settings
  problems/       statements, hidden tests, pictures of every problem
  reactions/      stickers and videos uploaded by the teacher
  manifest.json   when it was made and what is inside (counts)

The database is copied with SQLite's online backup API, not with a file copy:
the server can keep running and judging, and the copy is still consistent
(a plain copy taken in the middle of a write could be half old, half new).

  python3 tools/backup.py backup              # -> <database folder>/backups/ocj-backup-<time>.tar.gz
  python3 tools/backup.py list
  python3 tools/backup.py restore FILE --yes  # stop the server first!

In Docker (the backups folder is inside the judge-data volume, so it survives a rebuild):
  docker compose exec judge python3 tools/backup.py backup
  docker compose cp judge:/data/backups ./backups          # copy them to your computer
"""

import argparse
import datetime
import io
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import config  # noqa: E402

BACKUP_DIR = os.environ.get("JUDGE_BACKUP_DIR", os.path.join(os.path.dirname(os.path.abspath(config.DB_PATH)), "backups"))
KEEP = 10                     # older backups are deleted automatically


def _counts(db_path):
    con = sqlite3.connect(db_path)
    try:
        out = {}
        for table in ("users", "submissions", "contests", "reactions"):
            try:
                out[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                out[table] = 0
        return out
    finally:
        con.close()


def _add_dir(tar, path, arcname):
    if os.path.isdir(path):
        # Skip temporary files of an upload that is being written right now.
        tar.add(path, arcname=arcname, filter=lambda info: None if os.path.basename(info.name).startswith(".tmp-") else info)


def make_backup(out_dir=BACKUP_DIR, keep=KEEP):
    """Create one backup archive and return its path."""
    if not os.path.isfile(config.DB_PATH):
        raise SystemExit(f"no database at {config.DB_PATH}")
    os.makedirs(out_dir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    final = os.path.join(out_dir, f"ocj-backup-{stamp}.tar.gz")
    with tempfile.TemporaryDirectory() as tmp:
        # 1. A consistent snapshot of the live database (pages are copied while
        #    SQLite holds the right locks; writers are only paused briefly).
        snapshot = os.path.join(tmp, "judge.sqlite3")
        src = sqlite3.connect(config.DB_PATH, timeout=30)
        dst = sqlite3.connect(snapshot)
        with dst:
            src.backup(dst, pages=256, sleep=0.01)
        src.close()
        dst.close()
        manifest = {
            "made_at": time.time(), "made_at_text": datetime.datetime.now().isoformat(timespec="seconds"),
            "site": config.SITE_NAME, "database": os.path.abspath(config.DB_PATH),
            "counts": _counts(snapshot),
            "problems": sorted(n for n in os.listdir(config.PROBLEMS_DIR)
                               if os.path.isfile(os.path.join(config.PROBLEMS_DIR, n, "meta.json"))),
        }
        # 2. Everything into one compressed archive, written under a temporary
        #    name and renamed at the end: a crash never leaves a broken ".tar.gz".
        partial = final + ".part"
        with tarfile.open(partial, "w:gz") as tar:
            data = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size, info.mtime = len(data), int(time.time())
            tar.addfile(info, io.BytesIO(data))
            tar.add(snapshot, arcname="judge.sqlite3")
            _add_dir(tar, config.PROBLEMS_DIR, "problems")
            _add_dir(tar, config.REACTIONS_DIR, "reactions")
        os.replace(partial, final)
    # 3. Keep only the newest `keep` backups.
    olds = sorted(f for f in os.listdir(out_dir) if f.startswith("ocj-backup-") and f.endswith(".tar.gz"))
    for name in olds[:-keep] if keep > 0 else []:
        os.unlink(os.path.join(out_dir, name))
    return final, manifest


def read_manifest(path):
    with tarfile.open(path, "r:gz") as tar:
        return json.load(tar.extractfile("manifest.json"))


def list_backups(out_dir=BACKUP_DIR):
    if not os.path.isdir(out_dir):
        return []
    rows = []
    for name in sorted(os.listdir(out_dir)):
        if name.startswith("ocj-backup-") and name.endswith(".tar.gz"):
            path = os.path.join(out_dir, name)
            try:
                counts = read_manifest(path)["counts"]
            except (tarfile.TarError, KeyError, ValueError, OSError):
                counts = None
            rows.append((path, os.path.getsize(path), counts))
    return rows


def restore(path):
    """Replace the database, problems and reactions with the ones in a backup."""
    manifest = read_manifest(path)
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(path, "r:gz") as tar:
            # filter="data" refuses absolute paths, "../", links and device files:
            # a tampered archive cannot write outside the temporary folder.
            try:
                tar.extractall(tmp, filter="data")
            except tarfile.FilterError as exc:
                raise SystemExit(f"refused: this archive tries to write outside its folder ({exc}); nothing was changed")
        if not os.path.isfile(os.path.join(tmp, "judge.sqlite3")):
            raise SystemExit("this file is not a backup made by tools/backup.py; nothing was changed")
        db_new = os.path.join(tmp, "judge.sqlite3")
        if sqlite3.connect(db_new).execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit("the database in this backup is damaged; nothing was changed")
        # Keep what is there now, in case the restore was a mistake. (A fresh
        # install, e.g. a new server, has no database yet: nothing to keep.)
        safety = make_backup()[0] if os.path.isfile(config.DB_PATH) else None
        os.makedirs(os.path.dirname(os.path.abspath(config.DB_PATH)), exist_ok=True)
        for suffix in ("-wal", "-shm"):
            try:
                os.unlink(config.DB_PATH + suffix)
            except FileNotFoundError:
                pass
        shutil.copyfile(db_new, config.DB_PATH)
        for name, target in (("problems", config.PROBLEMS_DIR), ("reactions", config.REACTIONS_DIR)):
            source = os.path.join(tmp, name)
            if os.path.isdir(source):
                if os.path.isdir(target):
                    shutil.rmtree(target)
                shutil.copytree(source, target)
    return manifest, safety


def main():
    parser = argparse.ArgumentParser(description="Back up / restore the judge's data.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup", help="make a new backup")
    b.add_argument("--out", default=BACKUP_DIR, help=f"folder for the archive (default {BACKUP_DIR})")
    sub.add_parser("list", help="show the existing backups")
    r = sub.add_parser("restore", help="replace the current data with a backup (stop the server first)")
    r.add_argument("file")
    r.add_argument("--yes", action="store_true", help="really do it")
    args = parser.parse_args()

    if args.command == "backup":
        path, manifest = make_backup(args.out)
        c = manifest["counts"]
        print(f"Backup written: {path} ({os.path.getsize(path) / 1024:.0f} KB)")
        print(f"  {c['users']} accounts, {c['submissions']} submissions, {c['contests']} contests, "
              f"{c['reactions']} reactions, {len(manifest['problems'])} problems")
    elif args.command == "list":
        rows = list_backups()
        if not rows:
            print(f"No backups in {BACKUP_DIR}")
        for path, size, counts in rows:
            extra = "" if counts is None else f"  {counts['users']} accounts, {counts['submissions']} submissions"
            print(f"{os.path.basename(path)}  {size / 1024:8.0f} KB{extra}")
    elif args.command == "restore":
        if not args.yes:
            raise SystemExit("This REPLACES the database, the problems and the reactions.\n"
                             "Stop the server first, then run again with --yes.")
        manifest, safety = restore(args.file)
        print(f"Restored the backup made {manifest['made_at_text']}.")
        if safety:
            print(f"The data from before the restore was saved first: {safety}")
        print("Start the server again.")


if __name__ == "__main__":
    main()
