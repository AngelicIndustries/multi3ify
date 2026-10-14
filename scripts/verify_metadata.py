#!/usr/bin/env python3
"""Verify the complete served checksum chain and custom component dependencies."""
import argparse
import json
from pathlib import Path
import zipfile

from scripts.build_metadata import (MC_VERSION, GAME_VERSION, MC_ALIASES, LWJGL3IFY_UID, CLEANROOM_UID,
                                    ROOT, read_verified, reported_version, segment)
from scripts import cleanroom


def verify(root, *, require_cleanroom=False):
    packages = json.loads((root / "index.json").read_bytes())["packages"]
    versions = {}
    generated = []
    count = 0
    for package in packages:
        uid = segment(package["uid"])
        _, index = read_verified(root / uid / "index.json", package["sha256"])
        versions[uid] = {v["version"] for v in index["versions"]}
        for entry in index["versions"]:
            version = segment(entry["version"])
            _, document = read_verified(root / uid / f"{version}.json", entry["sha256"])
            if document["uid"] != uid or document["version"] != reported_version(uid, version):
                raise ValueError(f"Incorrect identity in {uid}/{version}")
            if ("lwjgl3ify" in version or version == cleanroom.BRIDGE_VERSION
                    or (uid == "net.minecraft" and version in MC_ALIASES)
                    or uid in {LWJGL3IFY_UID, CLEANROOM_UID}):
                if entry.get("requires") != document.get("requires"):
                    raise ValueError("Index and version requirements disagree")
                generated.append(document)
            count += 1
    for document in generated:
        if document["uid"] == "net.minecraft":
            if document["version"] not in MC_ALIASES.values():
                raise ValueError("Custom Minecraft must report the real game version for mod searches")
            if any(k in document for k in ("libraries", "mainClass", "compatibleJavaMajors", "compatibleJavaName")):
                raise ValueError("Custom Minecraft must leave runtime selection to its component")
        for requirement in document.get("requires", []):
            version = requirement.get("equals", requirement.get("suggests"))
            if version not in versions.get(requirement["uid"], set()):
                raise ValueError(f"Unresolved dependency: {requirement}")
        if document["uid"] == "net.minecraftforge":
            is_cleanroom = document["version"] == cleanroom.BRIDGE_VERSION
            game = cleanroom.GAME_VERSION if is_cleanroom else GAME_VERSION
            runtime_uid = CLEANROOM_UID if is_cleanroom else LWJGL3IFY_UID
            if {"uid": "net.minecraft", "equals": game} not in document["requires"]:
                raise ValueError("Forge must depend on Minecraft's reported game version")
            if {"uid": runtime_uid, "suggests": "latest"} not in document["requires"]:
                raise ValueError("Forge must install the independent runtime component")
            if "libraries" in document or "mainClass" in document or "+jvmArgs" in document:
                raise ValueError("Forge bridge must not override the selected runtime")
        if document["uid"] == CLEANROOM_UID:
            names = [lib["name"] for lib in document["libraries"]]
            if any(n.startswith(("org.lwjgl.lwjgl:", "net.minecraft:launchwrapper:", "net.minecraftforge:forge:",
                                 "io.github.jackofnonetrades:multi3ify-bootstrap:")) for n in names):
                raise ValueError("Obsolete or lwjgl3ify runtime in Cleanroom")
            if (document["mainClass"] != cleanroom.MAIN_CLASS or min(document["compatibleJavaMajors"]) < 25
                    or document["compatibleJavaName"] != cleanroom.JAVA_NAMES.get(document["compatibleJavaMajors"][0])
                    or any("multi3ify." in arg for arg in document["+jvmArgs"])):
                raise ValueError("Incorrect Cleanroom launch/Java configuration")
            if document["requires"] != [{"uid": "net.minecraft", "equals": cleanroom.GAME_VERSION}]:
                raise ValueError("Cleanroom must depend on Minecraft 1.12.2")
            for library in document["libraries"]:
                cleanroom.validate_library(library)
        if document["uid"] == LWJGL3IFY_UID:
            names = [lib["name"] for lib in document["libraries"]]
            if any(n.startswith(("org.lwjgl.lwjgl:", "net.minecraft:launchwrapper:", "org.ow2.asm:asm-all:")) for n in names):
                raise ValueError("Obsolete runtime library in lwjgl3ify")
            if 8 in document["compatibleJavaMajors"]:
                raise ValueError("Java 8 in lwjgl3ify")
            patches = next(i for i, n in enumerate(names) if n.endswith(":forgePatches"))
            forge = next(i for i, n in enumerate(names) if n.startswith("net.minecraftforge:forge:"))
            if patches >= forge:
                raise ValueError("Forge patches must precede Forge on the classpath")
    if MC_VERSION not in versions["net.minecraft"] or "latest" not in versions.get(LWJGL3IFY_UID, set()):
        raise ValueError("Missing custom Minecraft/lwjgl3ify entries")
    has_cleanroom = CLEANROOM_UID in versions
    if require_cleanroom or has_cleanroom:
        if (cleanroom.MC_VERSION not in versions["net.minecraft"]
                or "latest" not in versions.get(CLEANROOM_UID, set())):
            raise ValueError("Missing custom Minecraft/Cleanroom entries")
        minecraft = json.loads((root / "net.minecraft" / f"{cleanroom.MC_VERSION}.json").read_bytes())
        if minecraft["requires"] != [{"uid": "net.minecraftforge", "equals": cleanroom.BRIDGE_VERSION}]:
            raise ValueError("Cleanroom Minecraft must require its Forge bridge")
    if len([d for d in generated if d["uid"] == "net.minecraftforge"]) != 1 + has_cleanroom:
        raise ValueError("Forge picker must contain one additional entry per integration")
    with zipfile.ZipFile(ROOT / "metadata/legacy-forge.zip") as archive:
        for entry in json.loads(archive.read("index.json"))["versions"]:
            if entry["version"] in versions["net.minecraftforge"]:
                raise ValueError("Legacy releases must not clutter the Forge picker")
            read_verified(root / "net.minecraftforge" / (segment(entry["version"]) + ".json"), entry["sha256"])
    print(f"Verified {count} versions, {len(generated)} custom entries, and every SHA-256 link")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    verify(parser.parse_args().root, require_cleanroom=True)
