"""Per-unit split manifest.

JSON keys are kept camelCase so manifest files stay readable/interoperable
across tooling that reads them.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST_FILENAME = ".pdfslice-manifest.json"


@dataclass
class ManifestImageEntry:
    file: str  # file name relative to the manifest's folder
    page: int
    hash: str


@dataclass
class Manifest:
    version: int
    sourcePdf: str  # noqa: N815 - camelCase to match on-disk JSON schema
    sourcePdfHash: str  # noqa: N815
    pageCount: int  # noqa: N815
    images: list[ManifestImageEntry] = field(default_factory=list)
    updatedAt: str = ""  # noqa: N815
    gatheredAt: str | None = None  # noqa: N815 - set once `gather` has run at least once
    filenameTemplate: str = ""  # noqa: N815

    def to_json(self) -> str:
        data = {
            "version": self.version,
            "sourcePdf": self.sourcePdf,
            "sourcePdfHash": self.sourcePdfHash,
            "pageCount": self.pageCount,
            "images": [{"file": i.file, "page": i.page, "hash": i.hash} for i in self.images],
            "updatedAt": self.updatedAt,
            "gatheredAt": self.gatheredAt,
            "filenameTemplate": self.filenameTemplate,
        }
        # The pdfslice CLI omits gatheredAt until the first gather (JSON.stringify
        # drops undefined), and writes raw UTF-8; do the same so the files are identical.
        if data["gatheredAt"] is None:
            del data["gatheredAt"]
        return json.dumps(data, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        data = json.loads(text)
        return cls(
            version=data["version"],
            sourcePdf=data["sourcePdf"],
            sourcePdfHash=data["sourcePdfHash"],
            pageCount=data["pageCount"],
            images=[ManifestImageEntry(**i) for i in data.get("images", [])],
            updatedAt=data.get("updatedAt", ""),
            gatheredAt=data.get("gatheredAt"),
            filenameTemplate=data.get("filenameTemplate", ""),
        )


def manifest_path_for(folder: str | Path) -> Path:
    return Path(folder) / MANIFEST_FILENAME


def read_manifest(folder: str | Path) -> Manifest | None:
    p = manifest_path_for(folder)
    if not p.exists():
        return None
    return Manifest.from_json(p.read_text(encoding="utf-8"))


def write_manifest(folder: str | Path, manifest: Manifest) -> None:
    manifest_path_for(folder).write_text(manifest.to_json(), encoding="utf-8")
