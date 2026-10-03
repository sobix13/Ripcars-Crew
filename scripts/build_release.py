"""Build clean, inspectable release archives without data or credentials."""
from __future__ import annotations

import argparse
from pathlib import Path
import tarfile
import zipfile

EXCLUDED={".venv",".git","__pycache__",".pytest_cache","data","backups","releases"}


def files(root):
    return sorted(p for p in root.rglob("*") if p.is_file() and not any(k in EXCLUDED for k in p.relative_to(root).parts)
                  and p.name!=".env" and not p.name.endswith((".pyc",".pyo","_FA.md")) and ".sqlite" not in p.name and ".db" not in p.name)


def build(root,destination):
    root=Path(root).resolve();destination=Path(destination).resolve()
    if destination==root or root in destination.parents:
        raise ValueError("Archive destination must be outside the source directory.")
    version=(root/"VERSION").read_text().strip()
    destination.mkdir(parents=True,exist_ok=True)
    paths=files(root)
    for extension in ("tar.gz","zip"):
        if (destination/f"ripcars-crew-{version}.{extension}").exists():
            raise ValueError("An archive already exists. Choose a new destination.")
    tarpath=destination/f"ripcars-crew-{version}.tar.gz"
    zippath=destination/f"ripcars-crew-{version}.zip"
    with tarfile.open(tarpath,"w:gz") as archive:
        for p in paths:archive.add(p,arcname=str(Path("ripcars-crew")/p.relative_to(root)),recursive=False)
    with zipfile.ZipFile(zippath,"w",zipfile.ZIP_DEFLATED) as archive:
        for p in paths:archive.write(p,arcname=str(Path("ripcars-crew")/p.relative_to(root)))
    return tarpath,zippath,len(paths)


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination");args=parser.parse_args()
    for value in build(Path(__file__).resolve().parent.parent,args.destination):print(value)
