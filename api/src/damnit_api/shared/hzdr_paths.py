"""Map the paths events record onto the paths this host can open.

Producers record the path people use (``/bigdata/...``) or the one on their own
PC (``Z:/bigdata/...``); the server reaches the same share through a mount
(``/home/<user>/mnt/bigdata``). ``DW_API_METADATA__PATH_MAP`` states the
translation as ``from=to`` prefixes, comma separated, the way the SciCat
plugin's ``SOURCE_FOLDER_PUBLISH_MAP`` does in the other direction.
"""

from pathlib import Path, PurePosixPath, PureWindowsPath

PathRule = tuple[str, str]


def parse_path_map(spec: str) -> list[PathRule]:
    """Parse ``"/bigdata=/mnt/bigdata,Z:/bigdata=/mnt/bigdata"`` into rules."""
    rules: list[PathRule] = []
    for entry in spec.split(","):
        entry = entry.strip()
        if not entry:
            continue
        source, sep, target = entry.partition("=")
        source, target = source.strip(), target.strip()
        if not sep or not source or not target:
            msg = f"path map entry {entry!r} must look like 'from=to'"
            raise ValueError(msg)
        if not (
            PurePosixPath(target).is_absolute() or PureWindowsPath(target).is_absolute()
        ):
            msg = f"path map target {target!r} must be an absolute path"
            raise ValueError(msg)
        rules.append((source, target))
    return rules


def _normalize(raw: str) -> str:
    return raw.replace("\\", "/")


def map_path(raw: str | None, rules: list[PathRule]) -> Path | None:
    """Return ``raw`` with the longest matching prefix rule applied."""
    if raw is None:
        return None
    path = _normalize(raw)
    prefixed = [(_normalize(source).rstrip("/"), target) for source, target in rules]
    for prefix, target in sorted(prefixed, key=lambda rule: -len(rule[0])):
        head = path[: len(prefix)]
        # Drive letters compare case-insensitively; everything else exactly.
        same = (
            head.lower() == prefix.lower()
            if PureWindowsPath(prefix).drive
            else head == prefix
        )
        if same and (len(path) == len(prefix) or path[len(prefix)] == "/"):
            return Path(target) / path[len(prefix) :].lstrip("/")
    return Path(raw)
