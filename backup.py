"""Daily DB backup: consistent SQLite snapshot -> backups/hl_whales-YYYYMMDD.db.gz, keep last 14.

  python backup.py
"""
import gzip, shutil, sqlite3, time
from pathlib import Path

from ingest import DB

KEEP = 14
OUT = DB.with_name("backups")


def main():
    OUT.mkdir(exist_ok=True)
    tmp = OUT / "snapshot.tmp.db"
    src, dst = sqlite3.connect(f"file:{DB}?mode=ro", uri=True), sqlite3.connect(tmp)
    src.backup(dst)
    src.close()
    check = dst.execute("PRAGMA integrity_check").fetchone()[0]
    dst.close()
    if check != "ok":
        raise SystemExit(f"integrity check failed: {check}")

    gz = OUT / f"hl_whales-{time.strftime('%Y%m%d')}.db.gz"
    with open(tmp, "rb") as f, gzip.open(gz, "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g)
    tmp.unlink()
    shutil.copyfile(gz, OUT / "latest.db.gz")  # fixed name for off-site pulls

    for old in sorted(OUT.glob("hl_whales-*.db.gz"))[:-KEEP]:
        old.unlink()
    print(f"backup ok: {gz.name} {gz.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
