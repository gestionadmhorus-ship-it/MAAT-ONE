"""Detección de versiones estables de MAAT One en GitHub Releases."""

import json
from pathlib import Path
import re
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen


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
