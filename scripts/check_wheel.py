"""Проверить собранный wheel перед публикацией.

Проверяется то, что ломается только после установки из пакета:

1. в wheel лежат ``sources.yaml`` и ``prices.yaml`` (они читаются через
   ``importlib.resources`` и без них первая же команда падает);
2. ``METADATA`` содержит правильное имя дистрибутива, версию и лицензию;
3. в ``entry_points.txt`` объявлены все три консольные команды.

Запуск: ``python3 scripts/check_wheel.py [dist]`` (по умолчанию ``dist``).
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import NoReturn

REQUIRED_MODULES = ("taskhub/sources.yaml", "taskhub/prices.yaml")
REQUIRED_SCRIPTS = ("taskhub", "taskhub-mcp", "taskhub-web")


def _fail(message: str) -> NoReturn:
    print(f"[FAIL] {message}")
    raise SystemExit(1)


def check(dist: Path) -> None:
    """Проверить единственный wheel в ``dist``.

    Args:
        dist: Каталог артефактов ``uv build``.

    Raises:
        SystemExit: Если wheel не найден или не проходит проверку.
    """

    wheels = sorted(dist.glob("*.whl"))
    if len(wheels) != 1:
        _fail(f"expected exactly one wheel in {dist}, found {len(wheels)}")
    wheel = wheels[0]

    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        if metadata_name is None:
            _fail(f"{wheel.name}: no METADATA")
        metadata = archive.read(metadata_name).decode("utf-8")
        entry_points = next((n for n in names if n.endswith(".dist-info/entry_points.txt")), None)
        entry_text = ""
        if entry_points is not None:
            entry_text = archive.read(entry_points).decode("utf-8")

    for module in REQUIRED_MODULES:
        if module not in names:
            _fail(f"{wheel.name}: {module} is not packaged")

    fields = {
        key: value
        for line in metadata.splitlines()
        if ": " in line
        for key, value in (line.split(": ", 1),)
    }
    if fields.get("Name") != "taskhub-mcp":
        _fail(f"unexpected distribution name: {fields.get('Name')!r}")
    if fields.get("License-Expression") != "MIT":
        _fail(f"unexpected licence: {fields.get('License-Expression')!r}")
    if "License-File" not in fields:
        _fail("LICENSE is not attached to the metadata")

    for script in REQUIRED_SCRIPTS:
        if f"{script} =" not in entry_text:
            _fail(f"entry point {script} is missing")

    print(f"[OK] {wheel.name}")
    print(f"     version: {fields.get('Version')}")
    print(f"     licence: {fields.get('License-Expression')}")
    print(f"     bundled configs: {', '.join(REQUIRED_MODULES)}")


def main(argv: list[str] | None = None) -> int:
    """Точка входа CLI."""

    args = list(sys.argv[1:] if argv is None else argv)
    dist = Path(args[0]).expanduser() if args else Path("dist")
    if not dist.is_dir():
        _fail(f"no build directory: {dist} (run `uv build` first)")
    check(dist)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
