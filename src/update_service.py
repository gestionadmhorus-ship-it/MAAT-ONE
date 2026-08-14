"""Detección de versiones estables de MAAT One en GitHub Releases."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import stat
import tempfile
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile


GITHUB_OWNER = "gestionadmhorus-ship-it"
GITHUB_REPOSITORY = "MAAT-ONE"
LATEST_RELEASE_URL = (
    f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPOSITORY}"
    "/releases/latest"
)
UPDATE_CHECK_TIMEOUT_SECONDS = 5.0
SEMVER_PATTERN = re.compile(
    r"\A(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z"
)
SHA256_DIGEST_PATTERN = re.compile(r"\Asha256:[0-9a-f]{64}\Z", re.IGNORECASE)
UPDATE_LOG_DIRECTORY = ".maat_update"
UPDATE_LOG_NAME = "update.log"
REQUIRED_PACKAGE_FILES = frozenset(
    {"version.json", "app.py", "launcher.pyw"}
)


class UpdatePackageError(Exception):
    """Fallo controlado al descargar o validar un paquete de actualización."""


def parse_semver(value: object) -> tuple[int, int, int]:
    """Valida SemVer MAJOR.MINOR.PATCH y devuelve componentes numéricos."""
    if not isinstance(value, str):
        raise ValueError("La versión debe ser texto.")
    match = SEMVER_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ValueError("La versión no cumple MAJOR.MINOR.PATCH.")
    return tuple(int(component) for component in match.groups())


def normalize_release_tag(value: object) -> str:
    """Normaliza un tag SemVer, aceptando únicamente un prefijo v opcional."""
    if not isinstance(value, str):
        raise ValueError("El tag debe ser texto.")
    normalized = value.strip()
    if normalized[:1].casefold() == "v":
        normalized = normalized[1:]
    return ".".join(str(component) for component in parse_semver(normalized))


def read_local_version(install_root: str | Path) -> str:
    """Lee y valida la única versión local declarada en version.json."""
    version_path = Path(install_root) / "version.json"
    data = json.loads(version_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"version"}:
        raise ValueError("version.json no tiene la estructura esperada.")
    return ".".join(str(component) for component in parse_semver(data["version"]))


def fetch_latest_release(timeout: float = UPDATE_CHECK_TIMEOUT_SECONDS) -> dict | None:
    """Consulta la última Release estable; devuelve None si aún no existe."""
    request = Request(
        LATEST_RELEASE_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MAAT-One-update-check/1.0",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise
    if not isinstance(payload, dict):
        raise ValueError("GitHub no devolvió un objeto JSON.")
    return payload


def _release_assets(payload: dict) -> list[dict[str, object]]:
    raw_assets = payload.get("assets", [])
    if not isinstance(raw_assets, list):
        raise ValueError("La lista de assets de GitHub no es válida.")
    assets: list[dict[str, object]] = []
    for raw_asset in raw_assets:
        if not isinstance(raw_asset, dict):
            raise ValueError("GitHub devolvió un asset no válido.")
        name = raw_asset.get("name")
        download_url = raw_asset.get("browser_download_url")
        size = raw_asset.get("size")
        digest = raw_asset.get("digest")
        if (
            not isinstance(name, str)
            or not name
            or not isinstance(download_url, str)
            or not download_url.startswith("https://")
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
        ):
            raise ValueError("GitHub devolvió metadatos de asset no válidos.")
        if digest is not None and (
            not isinstance(digest, str)
            or SHA256_DIGEST_PATTERN.fullmatch(digest) is None
        ):
            digest = None
        assets.append(
            {
                "name": name,
                "download_url": download_url,
                "size": size,
                "digest": digest,
            }
        )
    return assets


def evaluate_release(local_version: object, payload: dict | None) -> dict[str, object]:
    """Compara una versión local con una respuesta estable ya obtenida."""
    normalized_local = ".".join(
        str(component) for component in parse_semver(local_version)
    )
    base_result: dict[str, object] = {
        "local_version": normalized_local,
        "remote_version": None,
        "tag_name": None,
        "assets": [],
    }
    if payload is None:
        return {**base_result, "status": "no_update", "reason": "no_release"}
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ValueError("La Release no es estable.")

    tag_name = payload.get("tag_name")
    normalized_remote = normalize_release_tag(tag_name)
    assets = _release_assets(payload)
    return {
        **base_result,
        "status": (
            "update_available"
            if parse_semver(normalized_remote) > parse_semver(normalized_local)
            else "no_update"
        ),
        "reason": None,
        "remote_version": normalized_remote,
        "tag_name": tag_name,
        "assets": assets,
    }


ReleaseFetcher = Callable[[float], dict | None]


def check_for_updates(
    install_root: str | Path,
    release_fetcher: ReleaseFetcher = fetch_latest_release,
    timeout: float = UPDATE_CHECK_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Comprueba actualizaciones sin propagar fallos de red o datos al launcher."""
    local_version: str | None = None
    try:
        local_version = read_local_version(install_root)
        payload = release_fetcher(timeout)
        return evaluate_release(local_version, payload)
    except (OSError, TimeoutError, ValueError, json.JSONDecodeError):
        return {
            "status": "update_check_unavailable",
            "reason": "check_failed",
            "local_version": local_version,
            "remote_version": None,
            "tag_name": None,
            "assets": [],
        }


def select_release_asset(update_check: dict[str, object]) -> dict[str, object] | None:
    """Selecciona únicamente el ZIP oficial exacto de la versión remota."""
    remote_version = update_check.get("remote_version")
    assets = update_check.get("assets")
    if not isinstance(remote_version, str) or not isinstance(assets, list):
        return None
    expected_name = f"MAAT-One-{remote_version}.zip"
    matches = [
        asset
        for asset in assets
        if isinstance(asset, dict) and asset.get("name") == expected_name
    ]
    return matches[0] if len(matches) == 1 else None


def _safe_zip_entries(archive: ZipFile) -> dict[str, object]:
    """Valida rutas y enlaces antes de leer contenido del paquete."""
    entries: dict[str, object] = {}
    for information in archive.infolist():
        raw_name = information.filename
        normalized_name = raw_name.replace("\\", "/")
        posix_path = PurePosixPath(normalized_name)
        windows_path = PureWindowsPath(raw_name)
        if (
            not normalized_name
            or "\x00" in normalized_name
            or posix_path.is_absolute()
            or windows_path.is_absolute()
            or bool(windows_path.drive)
            or ".." in posix_path.parts
        ):
            raise UpdatePackageError("zip_unsafe_path")
        mode = information.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise UpdatePackageError("zip_symbolic_link")
        canonical = posix_path.as_posix().rstrip("/")
        if canonical in entries:
            raise UpdatePackageError("zip_duplicate_entry")
        entries[canonical] = information
    return entries


def _validate_zip_package(
    package_path: Path,
    local_version: str,
    remote_version: str,
) -> None:
    """Valida integridad, estructura y versión sin extraer el ZIP."""
    try:
        with ZipFile(package_path) as archive:
            if archive.testzip() is not None:
                raise UpdatePackageError("zip_crc_failed")
            entries = _safe_zip_entries(archive)
            if not REQUIRED_PACKAGE_FILES.issubset(entries):
                raise UpdatePackageError("zip_required_files_missing")
            if not any(name.startswith("src/") for name in entries):
                raise UpdatePackageError("zip_src_missing")
            version_information = entries["version.json"]
            if getattr(version_information, "file_size", 0) > 65536:
                raise UpdatePackageError("zip_version_file_too_large")
            version_data = json.loads(archive.read(version_information).decode("utf-8"))
    except (BadZipFile, OSError, UnicodeError, json.JSONDecodeError) as error:
        raise UpdatePackageError("zip_invalid") from error

    if not isinstance(version_data, dict) or set(version_data) != {"version"}:
        raise UpdatePackageError("zip_version_structure_invalid")
    try:
        package_version = ".".join(
            str(component) for component in parse_semver(version_data["version"])
        )
    except ValueError as error:
        raise UpdatePackageError("zip_version_invalid") from error
    if package_version != remote_version:
        raise UpdatePackageError("zip_version_release_mismatch")
    if parse_semver(package_version) <= parse_semver(local_version):
        raise UpdatePackageError("zip_version_not_newer")


AssetOpener = Callable[..., object]


def download_and_validate_update(
    asset: dict[str, object],
    local_version: str,
    remote_version: str,
    asset_opener: AssetOpener = urlopen,
    timeout: float = UPDATE_CHECK_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Descarga en temporal, valida y elimina el ZIP sin activarlo."""
    name = asset.get("name")
    download_url = asset.get("download_url")
    expected_size = asset.get("size")
    expected_digest = asset.get("digest")
    if (
        name != f"MAAT-One-{remote_version}.zip"
        or not isinstance(download_url, str)
        or not download_url.startswith("https://")
        or not isinstance(expected_size, int)
        or isinstance(expected_size, bool)
        or expected_size < 0
        or (
            expected_digest is not None
            and (
                not isinstance(expected_digest, str)
                or SHA256_DIGEST_PATTERN.fullmatch(expected_digest) is None
            )
        )
    ):
        raise UpdatePackageError("asset_metadata_invalid")

    request = Request(
        download_url,
        headers={"User-Agent": "MAAT-One-update-download/1.0"},
    )
    with tempfile.TemporaryDirectory(prefix="maat-one-update-") as temporary:
        package_path = Path(temporary) / str(name)
        digest = sha256()
        downloaded_size = 0
        try:
            with asset_opener(request, timeout=timeout) as response:
                if getattr(response, "status", None) != 200:
                    raise UpdatePackageError("download_http_invalid")
                content_length = response.headers.get("Content-Length")
                if content_length is not None:
                    try:
                        declared_size = int(content_length)
                    except ValueError as error:
                        raise UpdatePackageError("download_length_invalid") from error
                    if declared_size != expected_size:
                        raise UpdatePackageError("download_length_mismatch")
                with package_path.open("wb") as destination:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                        digest.update(chunk)
                        downloaded_size += len(chunk)
        except UpdatePackageError:
            raise
        except Exception as error:
            raise UpdatePackageError("download_failed") from error

        if downloaded_size != expected_size:
            raise UpdatePackageError("download_size_mismatch")
        calculated_digest = "sha256:" + digest.hexdigest()
        if (
            expected_digest is not None
            and calculated_digest.casefold() != expected_digest.casefold()
        ):
            raise UpdatePackageError("download_digest_mismatch")
        _validate_zip_package(package_path, local_version, remote_version)
        return {
            "status": "update_validated",
            "asset_name": name,
            "size": downloaded_size,
            "digest": calculated_digest,
            "activated": False,
        }


def _write_update_log(install_root: str | Path, message: str) -> None:
    """Registra trazabilidad concisa sin bloquear el arranque si falla el log."""
    try:
        log_directory = Path(install_root) / UPDATE_LOG_DIRECTORY
        log_directory.mkdir(exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with (log_directory / UPDATE_LOG_NAME).open(
            "a", encoding="utf-8", newline="\n"
        ) as log_file:
            log_file.write(f"{timestamp} {message}\n")
    except OSError:
        pass


def process_updates_silently(
    install_root: str | Path,
    release_fetcher: ReleaseFetcher = fetch_latest_release,
    asset_opener: AssetOpener = urlopen,
    timeout: float = UPDATE_CHECK_TIMEOUT_SECONDS,
) -> dict[str, object]:
    """Detecta y valida una actualización sin modificar la versión activa."""
    update_check = check_for_updates(
        install_root,
        release_fetcher=release_fetcher,
        timeout=timeout,
    )
    _write_update_log(
        install_root,
        "check "
        f"local={update_check.get('local_version')} "
        f"status={update_check['status']} "
        f"remote={update_check.get('remote_version')}",
    )
    if update_check["status"] != "update_available":
        return update_check

    asset = select_release_asset(update_check)
    if asset is None:
        _write_update_log(install_root, "package skipped reason=asset_missing")
        return {**update_check, "package_status": "not_installable"}

    _write_update_log(install_root, f"download start asset={asset['name']}")
    try:
        validation = download_and_validate_update(
            asset,
            str(update_check["local_version"]),
            str(update_check["remote_version"]),
            asset_opener=asset_opener,
            timeout=timeout,
        )
    except UpdatePackageError as error:
        _write_update_log(
            install_root, f"package failed reason={error}"
        )
        return {
            **update_check,
            "package_status": "validation_failed",
            "failure_reason": str(error),
        }
    except Exception as error:
        failure_reason = f"unexpected_{type(error).__name__}"
        _write_update_log(
            install_root, f"package failed reason={failure_reason}"
        )
        return {
            **update_check,
            "package_status": "validation_failed",
            "failure_reason": failure_reason,
        }
    _write_update_log(
        install_root,
        f"package validated asset={validation['asset_name']} "
        f"size={validation['size']} activated=False",
    )
    return {**update_check, "package_status": "validated", **validation}
