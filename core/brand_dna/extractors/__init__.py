import ipaddress
import socket
from urllib.parse import urlparse


class SSRFBlockedError(Exception):
    pass


def validate_url_safe(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        raise SSRFBlockedError(f"Esquema no permitido: {parsed.scheme}")
    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError("URL sin hostname")
    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        raise SSRFBlockedError(f"No se pudo resolver: {hostname}")
    for _, _, _, _, sockaddr in resolved:
        ip = ipaddress.ip_address(sockaddr[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise SSRFBlockedError(f"IP bloqueada ({ip}) para {hostname}")
    return url
