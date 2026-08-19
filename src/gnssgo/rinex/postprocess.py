from __future__ import annotations

import gzip
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from gnssgo.exceptions import PostProcessError
from gnssgo.rinex.detect import detect_compression, is_compact_rinex, strip_compression
from gnssgo.rinex.validation import RinexValidationResult, validate_rinex_file


@dataclass(frozen=True)
class PostProcessResult:
    input_path: Path
    output_path: Path
    status: str
    steps: list[str] = field(default_factory=list)
    rinex: RinexValidationResult | None = None


class HatanakaBackend:
    def decompress(self, path: Path, *, keep_input: bool = False) -> Path:
        try:
            import hatanaka  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PostProcessError(
                "Compact RINEX restoration requires optional dependency: gnssgo[hatanaka]."
            ) from exc
        try:
            restored = hatanaka.decompress_on_disk(str(path), delete=not keep_input)
        except Exception as exc:  # noqa: BLE001 - optional library exposes broad failures.
            raise PostProcessError(f"Hatanaka restoration failed for {path.name}: {exc}") from exc
        return Path(restored)


class PostProcessor:
    def __init__(self, hatanaka_backend: HatanakaBackend | None = None) -> None:
        self.hatanaka_backend = hatanaka_backend or HatanakaBackend()

    def process(
        self,
        path: Path,
        *,
        keep_compressed: bool = False,
        expected_rinex_type: str | None = None,
        validate_rinex: bool = True,
    ) -> PostProcessResult:
        current = path
        steps: list[str] = []
        compression = detect_compression(current)
        if compression == ".gz":
            current = self._gunzip(current, keep_compressed=keep_compressed)
            steps.append("gzip")
        elif compression == ".Z":
            current = self._unlzw(current, keep_compressed=keep_compressed)
            steps.append("unix-z")
        elif compression == ".zip":
            current = self._unzip(
                current,
                keep_compressed=keep_compressed,
                expected_rinex_type=expected_rinex_type,
            )
            steps.append("zip")

        if is_compact_rinex(current):
            current = self.hatanaka_backend.decompress(current, keep_input=keep_compressed)
            steps.append("hatanaka")

        rinex = (
            validate_rinex_file(current, expected_type=expected_rinex_type)
            if validate_rinex
            else None
        )
        return PostProcessResult(
            input_path=path,
            output_path=current,
            status="validated" if rinex else "processed",
            steps=steps,
            rinex=rinex,
        )


    def _unzip(
        self,
        path: Path,
        *,
        keep_compressed: bool,
        expected_rinex_type: str | None,
    ) -> Path:
        """Extract a provider ZIP safely and return the most relevant RINEX file.

        Mexico RGNA hourly ZIPs contain both an observation and a navigation
        RINEX file; Uruguay archives may contain a single RINEX file.  Extract
        only regular files with basename-only targets to prevent path traversal,
        then select the observation/navigation member requested by the task.
        """
        try:
            archive = zipfile.ZipFile(path)
        except (OSError, zipfile.BadZipFile) as exc:
            raise PostProcessError(f"Invalid ZIP archive {path.name}: {exc}") from exc

        extracted: list[Path] = []
        try:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = Path(info.filename).name
                if not name or name in {".", ".."}:
                    continue
                target = path.parent / name
                with archive.open(info, "r") as src, target.open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                extracted.append(target)
        except Exception as exc:
            for target in extracted:
                target.unlink(missing_ok=True)
            raise PostProcessError(f"ZIP extraction failed for {path.name}: {exc}") from exc
        finally:
            archive.close()

        if not extracted:
            raise PostProcessError(f"ZIP archive contains no files: {path.name}")

        selected = self._select_zip_rinex(extracted, expected_rinex_type)
        if selected is None:
            names = ", ".join(item.name for item in extracted[:8])
            raise PostProcessError(
                f"ZIP archive {path.name} contains no matching RINEX file"
                + (f": {names}" if names else ".")
            )

        if not keep_compressed:
            path.unlink(missing_ok=True)
        return selected

    @staticmethod
    def _select_zip_rinex(
        paths: list[Path], expected_rinex_type: str | None
    ) -> Path | None:
        def score(candidate: Path) -> int:
            name = candidate.name.lower()
            value = 0
            if name.endswith((".rnx", ".crx")):
                value += 20
            if expected_rinex_type == "observation":
                if re.search(r"(?:_mo\.(?:rnx|crx)|\.\d{2}o)$", name):
                    value += 100
                if re.search(r"(?:_mn\.(?:rnx|crx)|\.\d{2}[nglqpc])$", name):
                    value -= 80
            elif expected_rinex_type == "navigation":
                if re.search(r"(?:_mn\.(?:rnx|crx)|\.\d{2}[nglqpc])$", name):
                    value += 100
                if re.search(r"(?:_mo\.(?:rnx|crx)|\.\d{2}o)$", name):
                    value -= 80
            return value

        ranked = sorted(paths, key=score, reverse=True)
        if not ranked:
            return None
        if expected_rinex_type is not None and score(ranked[0]) <= 0:
            return None
        return ranked[0]

    def _gunzip(self, path: Path, *, keep_compressed: bool) -> Path:
        target = path.with_name(strip_compression(path.name))
        with gzip.open(path, "rb") as src, target.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        if not keep_compressed:
            path.unlink(missing_ok=True)
        return target

    def _unlzw(self, path: Path, *, keep_compressed: bool) -> Path:
        try:
            import unlzw3  # type: ignore[import-not-found]
        except ImportError as exc:
            raise PostProcessError(
                "Unix .Z decompression requires optional dependency: gnssgo[unix-z]."
            ) from exc
        target = path.with_name(strip_compression(path.name))
        target.write_bytes(unlzw3.unlzw(path.read_bytes()))
        if not keep_compressed:
            path.unlink(missing_ok=True)
        return target
