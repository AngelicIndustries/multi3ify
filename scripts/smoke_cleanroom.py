#!/usr/bin/env python3
"""Linux/x86_64 Cleanroom launch checks, with isolated empty and modded instances.

Run with Java 25: xvfb-run -a python -m scripts.smoke_cleanroom public
The pinned companion mods below are test inputs only, never published metadata.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import time
import urllib.parse
import zipfile

from scripts import cleanroom
from scripts.build_metadata import CLEANROOM_UID, ROOT, segment
from scripts.smoke_launch import active, download


def newer_library(candidate, previous):
    """Resolve numeric Maven upgrades as Prism does, after platform filtering.

    Refuse ambiguous qualifier-only changes instead of guessing Prism's order.
    """
    versions = [lib["name"].split(":")[2] for lib in (candidate, previous)]
    matches = [re.fullmatch(r"(\d+(?:\.\d+)*)(?:[-.].*)?", v) for v in versions]
    if all(matches):
        numbers = [tuple(map(int, m[1].split("."))) for m in matches]
        width = max(map(len, numbers))
        left, right = [n + (0,) * (width - len(n)) for n in numbers]
        if left != right:
            return left > right
    raise ValueError(f"New duplicate library needs Prism version comparison: {versions}")


def library_tasks(libraries, work):
    """Prism resolves ordinary and extracted-native libraries separately."""
    selected = {}
    for library in libraries:
        if not active(library):
            continue
        parts = library["name"].split(":")
        key = (bool(library.get("natives")), *parts[:2], *parts[3:])
        previous = selected.get(key)
        if previous is not None and previous != library:
            if not newer_library(library, previous):
                continue
        selected[key] = library
    tasks, classpath, natives = [], [], []
    for library in selected.values():
        if library.get("natives"):
            classifier = library["natives"].get("linux")
            if not classifier:
                continue
            artifact = library["downloads"]["classifiers"][classifier.replace("${arch}", "64")]
            destination = natives
        else:
            artifact = library["downloads"]["artifact"]
            destination = classpath
        # Match the launcher's Maven layout; Forge locates its libraries root
        # relative to maven-artifact rather than simply using the classpath.
        group, name, version, *_ = library["name"].split(":")
        path = work / "libraries"
        for part in [*group.split("."), name, version]:
            path /= segment(part)
        path /= Path(urllib.parse.urlparse(artifact["url"]).path).name
        tasks.append((artifact, path))
        destination.append(path)
    return tasks, classpath, natives


def mod_hashes(game):
    return {str(p.relative_to(game)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (game / "mods").rglob("*") if p.is_file()}


def contained_dependencies(mods, fixtures):
    """Scalar legitimately extracts its bundled Scala jars into mods/1.12.2."""
    result = {}
    for mod in fixtures:
        with zipfile.ZipFile(mods / mod["filename"]) as jar:
            manifest = jar.read("META-INF/MANIFEST.MF").decode().replace("\r\n ", "")
            for line in manifest.splitlines():
                if line.startswith("ContainedDeps: "):
                    for name in line.split(": ", 1)[1].split():
                        result[f"mods/1.12.2/{segment(name)}"] = hashlib.sha256(jar.read(name)).hexdigest()
    return result


def launch(command, game, log_path, timeout, *, allow_missing_companions=False):
    xdg_data = game.parent / "xdg-data"
    xdg_data.mkdir(exist_ok=True)
    with log_path.open("w") as log:
        process = subprocess.Popen(command, cwd=game, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, "ALSOFT_DRIVERS": "null", "LIBGL_ALWAYS_SOFTWARE": "1",
                                        "XDG_DATA_HOME": str(xdg_data)})
        try:
            deadline = time.monotonic() + timeout
            loaded_at = None
            while time.monotonic() < deadline:
                output = log_path.read_text(errors="replace")
                if process.poll() is not None:
                    break
                if "Fugue is missing" in output or "Scalar is missing" in output:
                    if not allow_missing_companions:
                        raise RuntimeError(f"Test companions were not loaded; full log: {log_path}")
                if "Forge Mod Loader has successfully loaded" in output:
                    if loaded_at is None:
                        loaded_at = time.monotonic()
                    if time.monotonic() - loaded_at >= 5:
                        return
                time.sleep(1)
            print(log_path.read_text(errors="replace")[-24000:])
            raise RuntimeError(f"Cleanroom did not finish initialization; full log: {log_path}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def smoke(site, work, java, timeout):
    site, work = site.resolve(), work.resolve()
    work.mkdir(parents=True, exist_ok=True)
    minecraft = json.loads((site / "v1/net.minecraft" / f"{cleanroom.MC_VERSION}.json").read_bytes())
    bridge = json.loads((site / "v1/net.minecraftforge" / f"{cleanroom.BRIDGE_VERSION}.json").read_bytes())
    if (minecraft["version"] != cleanroom.GAME_VERSION
            or minecraft["requires"] != [{"uid": "net.minecraftforge", "equals": bridge["version"]}]
            or {"uid": CLEANROOM_UID, "suggests": "latest"} not in bridge["requires"]):
        raise ValueError("Incorrect Cleanroom dependency chain")
    status = json.loads((site / "status.json").read_bytes())["cleanroom"]
    runtime = json.loads((site / "v1" / CLEANROOM_UID / "latest.json").read_bytes())
    pinned = json.loads((site / "v1" / CLEANROOM_UID / f"{status['latest']}.json").read_bytes())
    if {**pinned, "version": "latest"} != runtime:
        raise ValueError("Latest and its numbered Cleanroom release disagree")
    tasks, classpath, native_jars = library_tasks([*runtime["libraries"], minecraft["mainJar"]], work)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda task: download(*task), tasks))
    natives = work / "natives"
    natives.mkdir(exist_ok=True)
    for path in native_jars:
        with zipfile.ZipFile(path) as jar:
            for name in jar.namelist():
                if name.endswith(".so"):
                    (natives / Path(name).name).write_bytes(jar.read(name))
    assets = work / "assets"
    asset_index = minecraft["assetIndex"]
    index = download(asset_index, assets / "indexes" / (asset_index["id"] + ".json"))
    def asset(info):
        digest = info["hash"]
        download({"url": f"https://resources.download.minecraft.net/{digest[:2]}/{digest}",
                  "size": info["size"], "sha1": digest}, assets / "objects" / digest[:2] / digest)
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(asset, json.loads(index.read_bytes())["objects"].values()))
    fixtures = json.loads((ROOT / "tests/fixtures/cleanroom-mods.json").read_bytes())
    for variant, selected in (("empty", runtime), ("modded", pinned)):
        game = work / variant
        game.mkdir(exist_ok=True)
        # Keep splash UI races out of the isolated integration checks: 0.6.13
        # can clear warningPrompt during rendering, and 0.7.4's SDL event pump
        # can race Foundation's transformer registration on the splash thread.
        # Both variants still have to finish loading every mod and the renderer.
        config = game / "config"
        config.mkdir(exist_ok=True)
        (config / "splash.properties").write_text("enabled=false\n")
        mods = game / "mods"
        mods.mkdir(exist_ok=True)
        if variant == "modded":
            for mod in fixtures:
                download(mod, mods / mod["filename"])
        expected = {f"mods/{m['filename']}": m["sha256"] for m in fixtures} if variant == "modded" else {}
        allowed = expected | (contained_dependencies(mods, fixtures) if variant == "modded" else {})
        initial = mod_hashes(game)
        if not expected.items() <= initial.items() or any(allowed.get(p) != h for p, h in initial.items()):
            raise ValueError(f"Unexpected test mods in {game}; use a fresh work directory")
        args = {"auth_player_name": "MetadataSmokeTest", "version_name": cleanroom.GAME_VERSION,
                "game_directory": str(game), "assets_root": str(assets), "assets_index_name": asset_index["id"],
                "auth_uuid": "00000000000000000000000000000000", "auth_access_token": "0",
                "user_type": "legacy", "version_type": "release", "user_properties": "{}"}
        game_args = [re.sub(r"\$\{([^}]+)\}", lambda m: args[m[1]], arg)
                     for arg in shlex.split(minecraft["minecraftArguments"])]
        for tweaker in selected["+tweakers"]:
            game_args.extend(["--tweakClass", tweaker])
        command = [java, "-Xmx2G", f"-Djava.library.path={natives}", *selected["+jvmArgs"],
                   "-cp", os.pathsep.join(map(str, classpath)), selected["mainClass"], *game_args]
        launch(command, game, work / f"{variant}.log", timeout, allow_missing_companions=variant == "empty")
        if mod_hashes(game) != allowed:
            raise RuntimeError("Cleanroom changed test mods beyond their declared bundled dependencies")
        print(f"Cleanroom {status['latest']} initialized ({variant}, {selected['version']}); installed mod jars unchanged", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path)
    parser.add_argument("--work", type=Path, default=Path("build/cleanroom-smoke"))
    parser.add_argument("--java", default="java", help="Java 25+ executable")
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    smoke(args.site, args.work, args.java, args.timeout)
