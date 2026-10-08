#!/usr/bin/env python3
"""Build the exact archived C++ solver on macOS, linking installed HiGHS 1.15.1."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import io
import json
import subprocess
import sysconfig
import tarfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REVISION = "9e140fd66ff2789f0016153fdf167bbd9e2c6087"
UNITS = {
    "native_pattern_kernel": "NATIVE_PATTERN_KERNEL_LIBRARY",
    "native_restricted_master": "NATIVE_RESTRICTED_MASTER_LIBRARY",
    "full_cpp_solver_core": None,
    "native_feasibility_solver": None,
    "native_group_constructor": None,
    "native_rich_pattern_adapter": None,
    "native_rich_construction": None,
    "seat_protect_cpp": None,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    dest = args.output.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    paths = subprocess.check_output(
        ["git", "ls-tree", "-r", "--name-only", REVISION, "native"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    hashes = {}
    for relative in paths:
        if not relative.endswith((".cpp", ".hpp")):
            continue
        payload = subprocess.check_output(
            ["git", "show", f"{REVISION}:{relative}"], cwd=ROOT
        )
        path = dest / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        hashes[relative] = hashlib.sha256(payload).hexdigest()
    with urllib.request.urlopen(
        "https://pypi.org/pypi/highspy/1.15.1/json"
    ) as response:
        metadata = json.load(response)
    source = next(v for v in metadata["urls"] if v["packagetype"] == "sdist")
    archive = dest / "highspy-1.15.1.tar.gz"
    if not archive.exists():
        with urllib.request.urlopen(source["url"]) as response:
            archive.write_bytes(response.read())
    payload = archive.read_bytes()
    if hashlib.sha256(payload).hexdigest() != source["digests"]["sha256"]:
        raise ValueError("HiGHS source archive hash mismatch")
    with tarfile.open(fileobj=io.BytesIO(payload)) as tar:
        for item in tar.getmembers():
            if not (
                item.isfile()
                and item.name.startswith("highspy-1.15.1/highs/")
                and item.name.endswith((".h", ".hpp"))
            ):
                continue
            relative = Path(item.name).relative_to("highspy-1.15.1")
            if ".." in relative.parts:
                raise ValueError("Unsafe archive member")
            path = dest / "highs-headers" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(tar.extractfile(item).read())
    library = Path(sysconfig.get_paths()["purelib"]) / "highspy/libhighs.1.15.dylib"
    highs = ctypes.CDLL(str(library))
    highs.Highs_getSizeofHighsInt.restype = ctypes.c_int
    width = highs.Highs_getSizeofHighsInt()
    if width not in (4, 8):
        raise ValueError(f"Unsupported HiGHS integer width {width}")
    include = dest / "highs-headers/highs"
    # This generated header only supplies ABI width and version for the C API.
    (include / "HConfig.h").write_text(
        "#ifndef HCONFIG_H_\n#define HCONFIG_H_\n"
        + ("#define HIGHSINT64\n" if width == 8 else "")
        + "#define HIGHS_VERSION_MAJOR 1\n#define HIGHS_VERSION_MINOR 15\n"
        "#define HIGHS_VERSION_PATCH 1\n#endif\n"
    )
    provenance = {
        "revision": REVISION,
        "source_sha256": hashes,
        "headers_url": source["url"],
        "headers_sha256": source["digests"]["sha256"],
        "highs_library": str(library),
        "highs_library_sha256": hashlib.sha256(library.read_bytes()).hexdigest(),
        "highs_int_bytes": width,
        "solver_source_modified": False,
    }
    (dest / "preparation.json").write_text(json.dumps(provenance, indent=2))
    if args.prepare_only:
        print(f"Prepared unchanged historical sources in {dest}")
        return
    objects, commands = [], []
    for name, define in UNITS.items():
        obj = dest / f"{name}.o"
        command = [
            "clang++",
            "-std=c++17",
            "-O2",
            "-I",
            str(include),
            "-c",
            str(dest / "native" / f"{name}.cpp"),
            "-o",
            str(obj),
        ]
        if define:
            command.append(f"-D{define}")
        subprocess.run(command, check=True)
        commands.append(command)
        objects.append(str(obj))
    binary = dest / "seat_protect_cpp"
    command = [
        "clang++",
        *objects,
        str(library),
        f"-Wl,-rpath,{library.parent}",
        "-o",
        str(binary),
    ]
    subprocess.run(command, check=True)
    provenance.update(
        {
            "commands": [*commands, command],
            "compiler": subprocess.check_output(["clang++", "--version"], text=True),
            "binary": str(binary),
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        }
    )
    (dest / "build.json").write_text(json.dumps(provenance, indent=2))
    print(binary)


if __name__ == "__main__":
    main()
