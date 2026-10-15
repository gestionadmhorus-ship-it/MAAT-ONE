"""Construye el paquete ZIP oficial y reproducible de MAAT One."""

from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
import re
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIRECTORY = PROJECT_ROOT / "release_dist"
REQUIRED_ROOT_FILES = (
    "app.py",
    "launcher.pyw",
    "install.ps1",
    "version.json",
    "requirements.txt",
    "README.md",
)
SEMVER_PATTERN = re.compile(
    r"\A(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z"
)
FORBIDDEN_PARTS = frozenset(
    {
        ".git",
        ".github",
        ".venv",
        "venv",
        ".maat_update",
        "releases",
        "release_dist",
        "__pycache__",
        "build",
        "dist",
    }
)
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".log",
        ".lnk",
        ".jpg",
        ".jpeg",
        ".png",
        ".xlsx",
        ".xls",
        ".kmz",
        ".zip",
    }
)
NON_PORTABLE_PATH_PATTERNS = (
    re.compile(
        r"(?i)(?<![a-z0-9_])[a-z]:[\\/]+"
        r"(?![^\s\"']*<[^>\r\n]+>)(?=[^\\/\s\"'<>|])"
    ),
    re.compile(
        r"(?i)(?<![a-z0-9_.:\\/])(?:[\\/]{2,})"
        r"(?![^\s\"']*<[^>\r\n]+>)"
        r"[^\\/\s\"'<>|]+[\\/]+[^\\/\s\"'<>|]+"
    ),
)
FIXED_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


class ReleaseBuildError(Exception):
    """Error controlado de estructura o contenido del paquete oficial."""


def _read_version() -> str:
    try:
        data = json.loads(
            (PROJECT_ROOT / "version.json").read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseBuildError("version.json no es válido.") from error
    if not isinstance(data, dict) or set(data) != {"version"}:
        raise ReleaseBuildError("version.json no tiene la estructura esperada.")
    version = data["version"]
    if not isinstance(version, str) or SEMVER_PATTERN.fullmatch(version) is None:
        raise ReleaseBuildError("La versión no cumple MAJOR.MINOR.PATCH.")
    return version


def _package_files() -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for name in REQUIRED_ROOT_FILES:
        path = PROJECT_ROOT / name
        if not path.is_file() or path.is_symlink():
            raise ReleaseBuildError(f"Falta un archivo obligatorio: {name}")
        files.append((path, name))

    source_root = PROJECT_ROOT / "src"
    if not source_root.is_dir() or source_root.is_symlink():
        raise ReleaseBuildError("Falta la carpeta src.")
    for path in source_root.rglob("*"):
        if path.is_dir():
            continue
        relative = path.relative_to(PROJECT_ROOT)
        archive_name = relative.as_posix()
        if path.is_symlink():
            raise ReleaseBuildError(f"No se permiten enlaces: {archive_name}")
        if any(part.casefold() in FORBIDDEN_PARTS for part in relative.parts):
            continue
        if path.suffix.casefold() in {".pyc", ".pyo"}:
            continue
        if path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            raise ReleaseBuildError(
                f"Archivo prohibido dentro de src: {archive_name}"
            )
        files.append((path, archive_name))
    if not any(name.startswith("src/") for _path, name in files):
        raise ReleaseBuildError("La carpeta src no contiene archivos.")
    return sorted(files, key=lambda item: item[1].casefold())


def _validate_sources(files: list[tuple[Path, str]]) -> None:
    for path, archive_name in files:
        pure_path = PurePosixPath(archive_name)
        if pure_path.is_absolute() or ".." in pure_path.parts:
            raise ReleaseBuildError(f"Ruta no segura: {archive_name}")
        content = path.read_bytes()
        if path.suffix.casefold() in {".py", ".pyw"}:
            try:
                compile(content, str(path), "exec")
            except SyntaxError as error:
                raise ReleaseBuildError(
                    f"Sintaxis Python no válida: {archive_name}"
                ) from error
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if any(pattern.search(text) for pattern in NON_PORTABLE_PATH_PATTERNS):
            raise ReleaseBuildError(
                f"Se encontró una ruta absoluta no portable en: {archive_name}"
            )


def _zip_information(archive_name: str) -> ZipInfo:
    information = ZipInfo(archive_name, date_time=FIXED_ZIP_TIMESTAMP)
    information.compress_type = ZIP_DEFLATED
    information.create_system = 3
    information.external_attr = 0o100644 << 16
    return information


def build_release() -> dict[str, object]:
    """Valida las fuentes y publica ZIP más digest mediante reemplazo final."""
    version = _read_version()
    files = _package_files()
    _validate_sources(files)
    OUTPUT_DIRECTORY.mkdir(exist_ok=True)
    zip_name = f"MAAT-One-{version}.zip"
    zip_path = OUTPUT_DIRECTORY / zip_name
    digest_path = OUTPUT_DIRECTORY / f"{zip_name}.sha256"
    temporary_zip: Path | None = None
    temporary_digest: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{zip_name}.",
            suffix=".tmp",
            dir=OUTPUT_DIRECTORY,
            delete=False,
        ) as temporary:
            temporary_zip = Path(temporary.name)
        with ZipFile(
            temporary_zip,
            "w",
            compression=ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for source, archive_name in files:
                archive.writestr(
                    _zip_information(archive_name),
                    source.read_bytes(),
                    compress_type=ZIP_DEFLATED,
                    compresslevel=9,
                )
        digest = sha256(temporary_zip.read_bytes()).hexdigest()
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="ascii",
            newline="\n",
            prefix=f".{zip_name}.sha256.",
            suffix=".tmp",
            dir=OUTPUT_DIRECTORY,
            delete=False,
        ) as temporary:
            temporary_digest = Path(temporary.name)
            temporary.write(f"{digest}  {zip_name}\n")
        temporary_zip.replace(zip_path)
        temporary_zip = None
        temporary_digest.replace(digest_path)
        temporary_digest = None
    finally:
        if temporary_zip is not None:
            temporary_zip.unlink(missing_ok=True)
        if temporary_digest is not None:
            temporary_digest.unlink(missing_ok=True)
    return {
        "version": version,
        "zip_path": str(zip_path),
        "digest_path": str(digest_path),
        "sha256": digest,
        "entries": [archive_name for _path, archive_name in files],
    }


if __name__ == "__main__":
    result = build_release()
    print(f"Paquete: {result['zip_path']}")
    print(f"SHA-256: {result['sha256']}")
