"""Detección de versiones estables de MAAT One en GitHub Releases."""

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
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
CURRENT_STATE_NAME = "current.json"
CURRENT_BACKUP_NAME = "current.json.backup"
RELEASES_DIRECTORY = "releases"
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
    """Lee la versión declarada por el release activo."""
    version_path = resolve_active_release(install_root) / "version.json"
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


def _read_version_file(version_path: Path) -> str:
    data = json.loads(version_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or set(data) != {"version"}:
        raise ValueError("version.json no tiene la estructura esperada.")
    return ".".join(str(part) for part in parse_semver(data["version"]))


def _validated_current_data(data: object) -> dict[str, str | None]:
    if not isinstance(data, dict) or set(data) != {
        "active_version",
        "previous_version",
    }:
        raise ValueError("current.json no tiene la estructura esperada.")
    active = data["active_version"]
    previous = data["previous_version"]
    active_version = ".".join(str(part) for part in parse_semver(active))
    previous_version = None
    if previous is not None:
        previous_version = ".".join(
            str(part) for part in parse_semver(previous)
        )
    if previous_version == active_version:
        raise ValueError("Las versiones activa y anterior no pueden coincidir.")
    return {
        "active_version": active_version,
        "previous_version": previous_version,
    }


def _atomic_json_replace(path: Path, data: dict[str, object]) -> None:
    """Publica JSON completo mediante replace en el mismo volumen."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
            delete=False,
        ) as temporary:
            json.dump(data, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            temporary_path = Path(temporary.name)
        loaded = json.loads(temporary_path.read_text(encoding="utf-8"))
        _validated_current_data(loaded)
        temporary_path.replace(path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def read_current_state(
    install_root: str | Path,
    recover: bool = True,
) -> dict[str, str | None]:
    """Lee current.json y recupera sólo desde su backup validado."""
    root = Path(install_root)
    current_path = root / CURRENT_STATE_NAME
    try:
        return _validated_current_data(
            json.loads(current_path.read_text(encoding="utf-8"))
        )
    except (OSError, ValueError, json.JSONDecodeError):
        if not recover:
            raise
    backup_path = root / UPDATE_LOG_DIRECTORY / CURRENT_BACKUP_NAME
    backup = _validated_current_data(
        json.loads(backup_path.read_text(encoding="utf-8"))
    )
    _atomic_json_replace(current_path, backup)
    _write_update_log(root, f"current recovered active={backup['active_version']}")
    return backup


def _validate_release_directory(release_root: Path, expected_version: str) -> None:
    """Comprueba que una versión preparada es completa y coherente."""
    if not release_root.is_dir():
        raise UpdatePackageError("release_directory_missing")
    for relative in ("app.py", "version.json", "requirements.txt"):
        if not (release_root / relative).is_file():
            raise UpdatePackageError("release_required_files_missing")
    if not (release_root / "src").is_dir():
        raise UpdatePackageError("release_src_missing")
    try:
        actual_version = _read_version_file(release_root / "version.json")
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise UpdatePackageError("release_version_invalid") from error
    if actual_version != expected_version:
        raise UpdatePackageError("release_version_mismatch")


def resolve_active_release(install_root: str | Path) -> Path:
    """Resuelve exclusivamente la carpeta indicada por current.json."""
    root = Path(install_root).resolve()
    state = read_current_state(root)
    release_root = root / RELEASES_DIRECTORY / str(state["active_version"])
    _validate_release_directory(release_root, str(state["active_version"]))
    return release_root


def migrate_flat_installation(install_root: str | Path) -> Path:
    """Copia la versión semilla plana a releases/ sin tocar runtime ni datos."""
    root = Path(install_root).resolve()
    seed_version = _read_version_file(root / "version.json")
    releases_root = root / RELEASES_DIRECTORY
    releases_root.mkdir(exist_ok=True)
    destination = releases_root / seed_version
    if destination.exists():
        _validate_release_directory(destination, seed_version)
    else:
        staging = Path(
            tempfile.mkdtemp(prefix=f".{seed_version}.migrating-", dir=releases_root)
        )
        try:
            for name in ("app.py", "version.json", "requirements.txt"):
                shutil.copy2(root / name, staging / name)
            shutil.copytree(root / "src", staging / "src")
            readme = root / "README.md"
            if readme.is_file():
                shutil.copy2(readme, staging / "README.md")
            _validate_release_directory(staging, seed_version)
            staging.rename(destination)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    initial_state = {
        "active_version": seed_version,
        "previous_version": None,
    }
    current_path = root / CURRENT_STATE_NAME
    if current_path.exists():
        state = read_current_state(root)
        if state["active_version"] != seed_version and not (
            releases_root / str(state["active_version"])
        ).is_dir():
            raise UpdatePackageError("current_release_missing")
    else:
        _atomic_json_replace(current_path, initial_state)
    backup_path = root / UPDATE_LOG_DIRECTORY / CURRENT_BACKUP_NAME
    if not backup_path.exists():
        _atomic_json_replace(backup_path, read_current_state(root))
    _write_update_log(root, f"migration active={read_current_state(root)['active_version']}")
    return resolve_active_release(root)


def activate_release(install_root: str | Path, version: str) -> dict[str, str | None]:
    """Activa una versión completa mediante actualización atómica del puntero."""
    root = Path(install_root).resolve()
    normalized = ".".join(str(part) for part in parse_semver(version))
    _validate_release_directory(
        root / RELEASES_DIRECTORY / normalized,
        normalized,
    )
    current = read_current_state(root)
    if current["active_version"] == normalized:
        return current
    new_state = {
        "active_version": normalized,
        "previous_version": current["active_version"],
    }
    backup_path = root / UPDATE_LOG_DIRECTORY / CURRENT_BACKUP_NAME
    _atomic_json_replace(backup_path, current)
    _atomic_json_replace(root / CURRENT_STATE_NAME, new_state)
    _write_update_log(
        root,
        f"activated active={normalized} previous={current['active_version']}",
    )
    return new_state


def rollback_release(install_root: str | Path) -> dict[str, str | None]:
    """Revierte sólo a previous_version si existe y está validada."""
    root = Path(install_root).resolve()
    current = read_current_state(root)
    previous = current["previous_version"]
    if previous is None:
        raise UpdatePackageError("rollback_version_missing")
    _validate_release_directory(
        root / RELEASES_DIRECTORY / previous,
        previous,
    )
    rolled_back = {"active_version": previous, "previous_version": None}
    _atomic_json_replace(root / UPDATE_LOG_DIRECTORY / CURRENT_BACKUP_NAME, current)
    _atomic_json_replace(root / CURRENT_STATE_NAME, rolled_back)
    _write_update_log(
        root,
        f"rollback active={previous} failed={current['active_version']}",
    )
    return rolled_back


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


def prepare_release_from_zip(
    package_path: str | Path,
    install_root: str | Path,
    version: str,
) -> Path:
    """Extrae a staging validado y publica una carpeta de versión completa."""
    root = Path(install_root).resolve()
    normalized = ".".join(str(part) for part in parse_semver(version))
    releases_root = root / RELEASES_DIRECTORY
    releases_root.mkdir(exist_ok=True)
    destination = releases_root / normalized
    if destination.exists():
        _validate_release_directory(destination, normalized)
        return destination

    staging = Path(
        tempfile.mkdtemp(prefix=f".{normalized}.staging-", dir=releases_root)
    )
    try:
        with ZipFile(package_path) as archive:
            entries = _safe_zip_entries(archive)
            for relative_name, information in entries.items():
                target = staging / Path(*PurePosixPath(relative_name).parts)
                if information.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(information) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
        _validate_release_directory(staging, normalized)
        staging.rename(destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    _write_update_log(root, f"prepared version={normalized}")
    return destination


AssetOpener = Callable[..., object]
PackageHandler = Callable[[Path], dict[str, object]]


def download_and_validate_update(
    asset: dict[str, object],
    local_version: str,
    remote_version: str,
    asset_opener: AssetOpener = urlopen,
    timeout: float = UPDATE_CHECK_TIMEOUT_SECONDS,
    package_handler: PackageHandler | None = None,
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
        result: dict[str, object] = {
            "status": "update_validated",
            "asset_name": name,
            "size": downloaded_size,
            "digest": calculated_digest,
            "activated": False,
        }
        if package_handler is not None:
            result.update(package_handler(package_path))
        return result


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


def record_executed_version(install_root: str | Path, version: str) -> None:
    """Registra la versión cuya ejecución superó el control inicial."""
    _write_update_log(install_root, f"executed version={version}")


def process_updates_silently(
    install_root: str | Path,
    release_fetcher: ReleaseFetcher = fetch_latest_release,
    asset_opener: AssetOpener = urlopen,
    timeout: float = UPDATE_CHECK_TIMEOUT_SECONDS,
    smoke_test: Callable[[Path], bool] | None = None,
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

    def prepare_activate(package_path: Path) -> dict[str, object]:
        release_root = prepare_release_from_zip(
            package_path,
            install_root,
            str(update_check["remote_version"]),
        )
        if smoke_test is not None and not smoke_test(release_root):
            raise UpdatePackageError("release_smoke_test_failed")
        state = activate_release(
            install_root,
            str(update_check["remote_version"]),
        )
        return {
            "activated": True,
            "active_version": state["active_version"],
            "previous_version": state["previous_version"],
        }

    try:
        validation = download_and_validate_update(
            asset,
            str(update_check["local_version"]),
            str(update_check["remote_version"]),
            asset_opener=asset_opener,
            timeout=timeout,
            package_handler=prepare_activate,
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
        f"size={validation['size']} activated={validation['activated']}",
    )
    return {**update_check, "package_status": "validated", **validation}
