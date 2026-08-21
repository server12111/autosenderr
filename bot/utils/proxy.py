import re

_IPV6_HOST_RE = re.compile(r"^\[([0-9a-fA-F:]+)\](.*)$")


def _format_host(host: str) -> str:
    # IPv6 must stay bracketed in the socks5:// URL, otherwise urlparse
    # downstream can't tell the address's own colons from the port separator.
    return f"[{host}]" if ":" in host else host


def normalize_proxy_string(text: str) -> str:
    """Accepts either socks5://[user:pass@]host:port or the common
    seller-format host:port:user:pass / host:port (also tolerating spaces
    around ':' and a password that itself contains colons) and converts
    the latter into socks5://... . An IPv6 host must be bracketed
    ([::1]:port:user:pass), same as the URL form requires. Returns the
    input unchanged if it doesn't match any of these shapes."""
    text = text.strip()
    if text.startswith("socks5://"):
        return text

    ipv6_match = _IPV6_HOST_RE.match(text)
    if ipv6_match:
        host = ipv6_match.group(1)
        remainder = ipv6_match.group(2).strip().lstrip(":")
        parts = [p.strip() for p in remainder.split(":")] if remainder else []
    else:
        raw_parts = [p.strip() for p in text.split(":")]
        if len(raw_parts) < 2:
            return text
        host, parts = raw_parts[0], raw_parts[1:]

    if not parts or not parts[0].isdigit():
        return text
    port = parts[0]
    rest = parts[1:]

    if not rest:
        return f"socks5://{_format_host(host)}:{port}"

    user = rest[0]
    password = ":".join(rest[1:])
    if not password:
        return text
    return f"socks5://{user}:{password}@{_format_host(host)}:{port}"
