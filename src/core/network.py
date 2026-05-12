"""Network policy boundaries for business APIs and GitHub updates."""

import os
import ssl
import sys
import threading
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import MutableMapping

from core.log import logger

try:
    import certifi
except ImportError:  # pragma: no cover - source checkouts may not have deps installed.
    certifi = None


_PROXY_ENV_VARS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
_NO_PROXY_ENV_VARS = ("NO_PROXY", "no_proxy")
_UPDATE_PROXY_SCHEMES = ("http", "https")
_NETWORK_ENV_LOCK = threading.RLock()


def configure_direct_business_traffic(environ: MutableMapping[str, str] | None = None) -> None:
    """Make non-update API clients prefer direct connections."""
    env = os.environ if environ is None else environ
    with _NETWORK_ENV_LOCK:
        for name in _PROXY_ENV_VARS:
            env.pop(name, None)
        for name in _NO_PROXY_ENV_VARS:
            env[name] = "*"


@contextmanager
def direct_business_network(environ: MutableMapping[str, str] | None = None):
    """Temporarily enforce direct networking for a single business API call."""
    env = os.environ if environ is None else environ
    names = (*_PROXY_ENV_VARS, *_NO_PROXY_ENV_VARS)
    with _NETWORK_ENV_LOCK:
        saved = {name: env.get(name) for name in names}
        configure_direct_business_traffic(env)
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is None:
                    env.pop(name, None)
                else:
                    env[name] = value


def _filter_update_proxies(proxies: dict[str, str]) -> dict[str, str]:
    return {
        scheme: proxy
        for scheme, proxy in (proxies or {}).items()
        if scheme.lower() in _UPDATE_PROXY_SCHEMES and proxy
    }


def windows_system_update_proxies() -> dict[str, str]:
    if sys.platform != "win32":
        return {}
    getter = getattr(urllib.request, "getproxies_registry", None)
    if getter is None:
        return {}
    try:
        return _filter_update_proxies(getter())
    except OSError as e:
        logger.debug(f"[Network] Failed to read Windows system proxy: {e}")
        return {}


@contextmanager
def _without_no_proxy_env():
    with _NETWORK_ENV_LOCK:
        saved = {name: os.environ.pop(name, None) for name in _NO_PROXY_ENV_VARS}
        try:
            yield
        finally:
            for name, value in saved.items():
                if value is not None:
                    os.environ[name] = value


def resolve_update_proxies() -> dict[str, str]:
    """Resolve proxy settings for GitHub update traffic only."""
    proxies = windows_system_update_proxies()
    if proxies:
        return proxies
    with _without_no_proxy_env():
        return _filter_update_proxies(urllib.request.getproxies())


def create_update_ssl_context() -> ssl.SSLContext:
    """Use system trust plus certifi for bundled Python reliability."""
    context = ssl.create_default_context()
    if certifi is not None:
        try:
            cafile = certifi.where()
            if cafile and Path(cafile).exists():
                context.load_verify_locations(cafile=cafile)
        except Exception as e:
            logger.debug(f"[Network] Failed to load certifi CA bundle: {e}")
    return context


def open_update_url(req: urllib.request.Request, *, timeout: int):
    """Open a GitHub update URL through the update-only network path."""
    proxies = resolve_update_proxies()
    context = create_update_ssl_context()
    if not proxies:
        logger.debug("[Network] Opening update URL without proxy")
        return urllib.request.urlopen(req, timeout=timeout, context=context)

    logger.debug(f"[Network] Opening update URL with proxy schemes: {sorted(proxies)}")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler(proxies),
        urllib.request.HTTPSHandler(context=context),
    )
    with _without_no_proxy_env():
        return opener.open(req, timeout=timeout)
