#!/usr/bin/env python3
"""Linux/x86_64 integration check: boot the latest profile under xvfb.

Uses an isolated offline test instance, never a real launcher account/instance.
Run: xvfb-run -a python -m scripts.smoke_launch public [--liteloader]
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
import tempfile
import time
import urllib.parse
import zipfile

from scripts.build_metadata import (fetch, MC_VERSION, MC_LITELOADER_VERSION, GAME_VERSION, LWJGL3IFY_UID,
                                    BOOTSTRAP_CLASS, LITELOADER_UID)


def download(artifact, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    def valid(data):
        return (len(data) == artifact.get("size", len(data))
                and (not artifact.get("sha256") or hashlib.sha256(data).hexdigest() == artifact["sha256"])
                and (not artifact.get("sha1") or hashlib.sha1(data).hexdigest() == artifact["sha1"]))
    if path.exists() and valid(path.read_bytes()):
        return path
    data = fetch(artifact["url"])
    if not valid(data):
        raise ValueError(f"Download checksum mismatch: {artifact['url']}")
    path.write_bytes(data)
    return path


def active(library):
    rules = library.get("rules")
    if not rules:
        return True
    allowed = False
    for rule in rules:
        if rule.get("os", {}).get("name", "linux") == "linux":
            allowed = rule["action"] == "allow"
    return allowed


def check_legacy_conflict(site, work, classpath):
    """Exercise the real jars from an ordinary instance with only Forge changed."""
    vanilla = json.loads((site / f"v1/net.minecraft/{GAME_VERSION}.json").read_bytes())
    version = next(r["suggests"] for r in vanilla["requires"] if r["uid"] == "org.lwjgl")
    legacy = json.loads((site / f"v1/org.lwjgl/{version}.json").read_bytes())
    library = next(lib for lib in legacy["libraries"] if lib["name"].startswith("org.lwjgl.lwjgl:lwjgl:"))
    artifact = library["downloads"]["artifact"]
    path = download(artifact, work / "libraries" / Path(urllib.parse.urlparse(artifact["url"]).path).name)
    with tempfile.TemporaryDirectory(prefix="legacy-conflict-", dir=work) as tmp:
        game = Path(tmp) / "game"
        for mixed in ([str(path), *classpath], [*classpath, str(path)]):
            result = subprocess.run(["java", "-cp", os.pathsep.join(mixed), BOOTSTRAP_CLASS,
                                     "--gameDir", str(game)], capture_output=True, text=True, timeout=30)
            if (result.returncode == 0 or "Minecraft -> Change version -> 1.7.10-lwjgl3ify" not in result.stderr
                    or "Exception in thread" in result.stderr or "VerifyError" in result.stderr or game.exists()):
                raise RuntimeError(f"Mixed LWJGL runtime was not rejected before mod installation:\n{result.stderr}")
    print("Real LWJGL 2/3 conflict rejected in both classpath orders before changing instance files", flush=True)


def smoke(site, work, timeout, liteloader=False):
    site, work = site.resolve(), work.resolve()
    game = work / ("game-liteloader" if liteloader else "game")
    game.mkdir(parents=True, exist_ok=True)
    status = json.loads((site / "status.json").read_bytes())
    profile = MC_LITELOADER_VERSION if liteloader else MC_VERSION
    minecraft = json.loads((site / f"v1/net.minecraft/{profile}.json").read_bytes())
    bridge_id = next(r["suggests"] for r in minecraft["requires"] if r["uid"] == "net.minecraftforge")
    bridge = json.loads((site / f"v1/net.minecraftforge/{bridge_id}.json").read_bytes())
    runtime_id = next(r["suggests"] for r in bridge["requires"] if r["uid"] == LWJGL3IFY_UID)
    runtime = json.loads((site / "v1" / LWJGL3IFY_UID / f"{runtime_id}.json").read_bytes())
    if minecraft["version"] != GAME_VERSION:
        raise ValueError("The mod browser must see Minecraft 1.7.10")
    # Follow the profile's LiteLoader dependency; Prism applies its arguments after lwjgl3ify's.
    extra_jvm_args = []
    if liteloader:
        variant = next(r["suggests"] for r in minecraft["requires"] if r["uid"] == LITELOADER_UID)
        extra_jvm_args = json.loads((site / "v1" / LITELOADER_UID / f"{variant}.json").read_bytes())["+jvmArgs"]
    libraries = {}
    for library in runtime["libraries"]:
        if not active(library):
            continue
        parts = library["name"].split(":")
        key = tuple(parts[:2] + parts[3:])
        # The current upstream duplicates only Guava; preserve first position while replacing with the later version.
        if key in libraries and library != libraries[key] and parts[:2] != ["com.google.guava", "guava"]:
            raise ValueError(f"New duplicate library needs launcher version comparison: {library['name']}")
        libraries[key] = library
    tasks, classpath, native_jars = [], [], []
    for library in [*libraries.values(), minecraft["mainJar"]]:
        downloads = library["downloads"]
        if "artifact" in downloads:
            artifact = downloads["artifact"]
            if library["name"].startswith("io.github.jackofnonetrades:multi3ify-bootstrap:"):
                path = site / ("maven/" + artifact["url"].split("/maven/", 1)[1])
            else:
                path = work / "libraries" / Path(urllib.parse.urlparse(artifact["url"]).path).name
                tasks.append((artifact, path))
            classpath.append(str(path))
        classifier = library.get("natives", {}).get("linux")
        if classifier:
            classifier = classifier.replace("${arch}", "64")
            artifact = downloads["classifiers"][classifier]
            path = work / "libraries" / Path(urllib.parse.urlparse(artifact["url"]).path).name
            tasks.append((artifact, path))
            native_jars.append(path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda task: download(*task), tasks))
    check_legacy_conflict(site, work, classpath)
    natives = work / "natives"
    natives.mkdir(exist_ok=True)
    for path in native_jars:
        with zipfile.ZipFile(path) as jar:
            for member in jar.namelist():
                if member.endswith(".so"):
                    (natives / Path(member).name).write_bytes(jar.read(member))
    assets = work / "assets"
    asset_index = minecraft["assetIndex"]
    index_path = download(asset_index, assets / "indexes" / (asset_index["id"] + ".json"))
    objects = json.loads(index_path.read_bytes())["objects"]
    def asset(item):
        name, info = item
        digest = info["hash"]
        path = download({"url": f"https://resources.download.minecraft.net/{digest[:2]}/{digest}",
                         "size": info["size"], "sha1": digest}, assets / "objects" / digest[:2] / digest)
        # 1.7.10 consumes the resource pack from virtual/legacy too.
        virtual = assets / "virtual" / "legacy" / name
        virtual.parent.mkdir(parents=True, exist_ok=True)
        virtual.write_bytes(path.read_bytes())
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(asset, objects.items()))
    helper = dict(arg[2:].split("=", 1) for arg in runtime["+jvmArgs"] if arg.startswith("-Dmulti3ify."))
    # An instance set up with the old instructions: the helper must replace
    # this manual copy rather than load UniMixins twice.
    manual = game / "mods" / f"+unimixins-all-1.7.10-{status['unimixins']}.jar"
    download({"url": helper["multi3ify.unimixinsUrl"], "sha256": helper["multi3ify.unimixinsSha256"]}, manual)
    args = {"auth_player_name": "MetadataSmokeTest", "version_name": minecraft["version"], "game_directory": str(game),
            "assets_root": str(assets), "assets_index_name": asset_index["id"],
            "auth_uuid": "00000000000000000000000000000000", "auth_access_token": "0",
            "user_properties": "{}", "user_type": "legacy"}
    game_args = [re.sub(r"\$\{([^}]+)\}", lambda m: args[m[1]], arg)
                 for arg in shlex.split(minecraft["minecraftArguments"])]
    for tweaker in runtime["+tweakers"]:
        game_args.extend(["--tweakClass", tweaker])
    command = ["java", "-Xmx2G", f"-Djava.library.path={natives}", *runtime["+jvmArgs"], *extra_jvm_args,
               "-cp", os.pathsep.join(classpath), runtime["mainClass"], *game_args]
    xdg_data = work / "xdg-data"
    xdg_data.mkdir(exist_ok=True)
    log_path = work / "launch.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(command, cwd=game, stdout=log, stderr=subprocess.STDOUT,
                                   env={**os.environ, "ALSOFT_DRIVERS": "null", "LIBGL_ALWAYS_SOFTWARE": "1",
                                        "XDG_DATA_HOME": str(xdg_data)})
        try:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                output = log_path.read_text(errors="replace")
                if "Forge Mod Loader has successfully loaded" in output:
                    managed_jars = ["lwjgl3ify-multi3ify-managed.jar", "+unimixins-multi3ify-managed.jar"]
                    if liteloader:
                        managed_jars += ["liteloader-multi3ify-managed.jar", "liteloaderloader-multi3ify-managed.jar"]
                    for managed in managed_jars:
                        if not (game / "mods" / managed).exists():
                            raise RuntimeError(f"The helper did not install {managed}")
                    if manual.exists() or not list((game / ".multi3ify-backups").glob("*-" + manual.name)):
                        raise RuntimeError("The helper did not back up the manual UniMixins copy")
                    if liteloader and ("LiteLoader begin POSTINIT" not in output
                                       or "[LiteloaderLoader]: Applying transformation" not in output):
                        print(output[-24000:])
                        raise RuntimeError("LiteLoader did not start through LiteloaderLoader")
                    extra = (f" and LiteloaderLoader {status['liteloader']['liteloaderloader']}"
                             if liteloader else "")
                    print(f"Forge and lwjgl3ify {status['latest']} initialized successfully with the managed "
                          f"UniMixins {status['unimixins']}, which replaced a manual copy{extra}")
                    return
                if process.poll() is not None:
                    break
                time.sleep(1)
            print(log_path.read_text(errors="replace")[-24000:])
            raise RuntimeError(f"Game did not finish Forge initialization; full log: {log_path}")
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("site", type=Path)
    parser.add_argument("--work", type=Path, default=Path("build/launch-smoke"))
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--liteloader", action="store_true", help="Also select the lwjgl3ify LiteLoader")
    options = parser.parse_args()
    smoke(options.site, options.work, options.timeout, options.liteloader)
