import re


def normalize_proxy_string(text: str) -> str:
    """Accepts either socks5://[user:pass@]host:port or the common
    seller-format host:port:user:pass / host:port and converts the
    latter into socks5://... . Returns the input unchanged if it
    doesn't match either shape."""
    text = text.strip()
    if text.startswith("socks5://"):
        return text

    parts = text.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        if port.isdigit():
            return f"socks5://{user}:{password}@{host}:{port}"
    elif len(parts) == 2:
        host, port = parts
        if port.isdigit():
            return f"socks5://{host}:{port}"

    return text
