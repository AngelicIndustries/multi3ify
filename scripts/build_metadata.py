#!/usr/bin/env python3
"""Build a static Prism metadata mirror with lwjgl3ify and Cleanroom profiles.

Python 3.11+, JDK 17+, and no third-party Python dependencies are required.
"""

import argparse
import copy
import hashlib
import http.client
import io
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
MC_VERSION = "1.7.10-lwjgl3ify"
GAME_VERSION = "1.7.10"
LWJGL3IFY_UID = "io.github.jackofnonetrades.lwjgl3ify"
BOOTSTRAP_CLASS = "io.github.jackofnonetrades.multi3ify.Bootstrap"
RELEASES_API = "https://api.github.com/repos/GTNewHorizons/lwjgl3ify/releases"
CACHE_SCHEMA = 1
MC_ALIASES = {MC_VERSION: GAME_VERSION, "1.12.2-cleanroom": "1.12.2"}
CLEANROOM_UID = "io.github.jackofnonetrades.cleanroom"
FJORD_PACKAGES = ("moe.yushi.authlibinjector", "org.unmojang.loki")


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = encoded(value)
    path.write_bytes(data)
    return sha256(data)


def segment(value):
    if (not isinstance(value, str) or not value or value in {".", ".."}
            or any(c in value for c in '/\\\x00') or any(ord(c) < 32 for c in value)):
        raise ValueError(f"Unsafe metadata path segment: {value!r}")
    return value


def read_verified(path, digest):
    if path.is_symlink():
        raise ValueError(f"Symlink in metadata: {path}")
    data = path.read_bytes()
    if sha256(data) != digest:
        raise ValueError(f"Metadata checksum mismatch: {path}")
    return data, json.loads(data)


def reported_version(uid, metadata_version):
    """Prism keeps the lookup ID separately from the version in the payload.

    Component::updateCachedData uses the payload version for mod searches and
    dependency comparisons. Only our custom Minecraft entries are aliases.
    """
    if uid == "net.minecraft":
        return MC_ALIASES.get(metadata_version, metadata_version)
    return metadata_version


def mirror(source, target, *, packages=None):
    """Copy every indexed file verbatim, checking the complete checksum chain."""
    root = json.loads((source / "index.json").read_bytes())
    if root.get("formatVersion") != 1:
        raise ValueError("Unsupported Prism metadata format")
    if packages is not None:
        missing = set(packages) - {p["uid"] for p in root["packages"]}
        if missing:
            raise ValueError(f"Missing supplemental metadata packages: {sorted(missing)}")
        root["packages"] = [p for p in root["packages"] if p["uid"] in packages]
        target_root = json.loads((target / "index.json").read_bytes())
        existing = {p["uid"] for p in target_root["packages"]}
        if existing.intersection(packages):
            raise ValueError("Supplemental metadata would overwrite an upstream package")
    count = 0
    for package in root["packages"]:
        uid = segment(package["uid"])
        data, index = read_verified(source / uid / "index.json", package["sha256"])
        if index["uid"] != uid:
            raise ValueError(f"Mismatched package UID: {uid}")
        (target / uid).mkdir(parents=True, exist_ok=True)
        (target / uid / "index.json").write_bytes(data)
        for entry in index["versions"]:
            version = segment(entry["version"])
            data, document = read_verified(source / uid / f"{version}.json", entry["sha256"])
            if document["uid"] != uid or document["version"] != reported_version(uid, version):
                raise ValueError(f"Mismatched version identity: {uid}/{version}")
            (target / uid / f"{version}.json").write_bytes(data)
            count += 1
    if packages is None:
        (target / "index.json").write_bytes((source / "index.json").read_bytes())
    else:
        target_root["packages"].extend(root["packages"])
        write_json(target / "index.json", target_root)
    return count


def fetch(url):
    headers = {"User-Agent": "multi3ify-metadata", "Accept": "application/vnd.github+json"}
    # Never send the repository token to release download/CDN hosts.
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, http.client.IncompleteRead) as error:
            if attempt == 3:
                error.add_note(f"Failed to fetch {url} after {attempt + 1} attempts")
                raise
            logging.warning("Download failed for %s (attempt %s/4): %s; retrying in %ss",
                            url, attempt + 1, error, 2 ** attempt)
            time.sleep(2 ** attempt)


def version_key(version):
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Expected a stable semantic version: {version}")
    return tuple(map(int, version.split(".")))


def releases(minimum):
    result = []
    page = 1
    while True:
        batch = json.loads(fetch(f"{RELEASES_API}?per_page=100&page={page}"))
        if not isinstance(batch, list):
            raise ValueError("Unexpected GitHub releases response")
        for release in batch:
            version = release["tag_name"].removeprefix("v")
            # Some upstream beta tags are not marked prerelease on GitHub.
            if (release["draft"] or release["prerelease"]
                    or not re.fullmatch(r"\d+\.\d+\.\d+", version)):
                continue
            if version_key(version) >= version_key(minimum):
                result.append(release)
        if len(batch) < 100:
            break
        page += 1
    if not result:
        raise ValueError("No supported stable lwjgl3ify releases found")
    return sorted(result, key=lambda r: version_key(r["tag_name"].removeprefix("v")), reverse=True)


def asset_bytes(asset):
    data = fetch(asset["browser_download_url"])
    if len(data) != asset["size"]:
        raise ValueError(f"Truncated release asset: {asset['name']}")
    digest = asset.get("digest")
    if digest and digest != "sha256:" + sha256(data):
        raise ValueError(f"Release asset checksum mismatch: {asset['name']}")
    return data


def release_profile(release, cache):
    version = release["tag_name"].removeprefix("v")
    names = {a["name"]: a for a in release["assets"]}
    required = [f"lwjgl3ify-{version}{suffix}" for suffix in ("-multimc.zip", ".jar", "-forgePatches.jar")]
    if any(name not in names for name in required):
        raise ValueError(f"Release {version} is missing required assets; refusing a partial publication")
    assets = [names[name] for name in required]
    # download_count and uploader fields change without changing an asset.
    fingerprints = [{k: a.get(k) for k in ("id", "name", "size", "updated_at", "digest", "browser_download_url")}
                    for a in assets]
    key = sha256(encoded({"schema": CACHE_SCHEMA, "assets": fingerprints}))
    cached = cache / f"{version}-{key}.json"
    if cached.exists():
        return json.loads(cached.read_bytes())
    archive = zipfile.ZipFile(io.BytesIO(asset_bytes(assets[0])))
    patches = [json.loads(archive.read(name)) for name in archive.namelist()
               if name.startswith("patches/") and name.endswith(".json")]
    by_uid = {p["uid"]: p for p in patches}
    expected = {"net.minecraft", "net.minecraftforge", "org.lwjgl3",
                "me.eigenraven.lwjgl3ify.forgepatches", "me.eigenraven.lwjgl3ify.launchargs"}
    if set(by_uid) != expected:
        raise ValueError(f"Upstream patch layout changed in {version}: {sorted(by_uid)}")
    early = by_uid["me.eigenraven.lwjgl3ify.forgepatches"]
    if len(early["libraries"]) != 1 or early["libraries"][0].get("MMC-hint") != "local":
        raise ValueError(f"Unexpected early classpath in {version}")
    patch_jar = archive.read(f"libraries/lwjgl3ify-{version}-forgePatches.jar")
    if len(patch_jar) != assets[2]["size"]:
        raise ValueError(f"Embedded Forge patches do not match release {version}")
    if assets[2].get("digest") and assets[2]["digest"] != "sha256:" + sha256(patch_jar):
        raise ValueError(f"Embedded Forge patches checksum mismatch in {version}")
    early["libraries"][0].pop("MMC-hint")
    early["libraries"][0]["downloads"] = {"artifact": {
        "url": assets[2]["browser_download_url"], "size": len(patch_jar),
        "sha1": hashlib.sha1(patch_jar).hexdigest(),
    }}
    mod = asset_bytes(assets[1])
    # Validate that we have the actual client mod, not an API/dev jar.
    with zipfile.ZipFile(io.BytesIO(mod)) as jar:
        manifest = jar.read("META-INF/MANIFEST.MF").decode()
        if "Lwjgl3ifyCoremod" not in manifest:
            raise ValueError(f"Missing lwjgl3ify coremod in {version}")
    result = {"version": version, "releaseTime": release["published_at"],
              "patches": sorted(patches, key=lambda p: p["order"]),
              "modUrl": assets[1]["browser_download_url"], "modSha256": sha256(mod)}
    write_json(cached, result)
    print(f"Prepared lwjgl3ify {version}", flush=True)
    return result


def build_bootstrap(output, site_url):
    """Use a content-addressed Maven version so published helper URLs never change."""
    with tempfile.TemporaryDirectory() as tmp:
        classes = Path(tmp) / "classes"
        classes.mkdir()
        subprocess.run(["javac", "--release", "8", "-d", str(classes),
                        str(ROOT / "bootstrap/src/io/github/jackofnonetrades/multi3ify/Bootstrap.java")], check=True)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as jar:
            for path in sorted(classes.rglob("*.class")):
                info = zipfile.ZipInfo(path.relative_to(classes).as_posix(), (1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                jar.writestr(info, path.read_bytes())
        data = buffer.getvalue()
    version = sha256(data)[:16]
    # Retain older helpers: clients may still have metadata from an earlier
    # deployment. CI commits these tiny, immutable jars after successful deploys.
    archive = ROOT / "bootstrap/releases"
    archive.mkdir(exist_ok=True)
    (archive / f"{version}.jar").write_bytes(data)
    relative = None
    for release in sorted(archive.glob("*.jar")):
        release_data = release.read_bytes()
        if sha256(release_data)[:16] != release.stem:
            raise ValueError(f"Archived helper checksum mismatch: {release}")
        release_path = (f"maven/io/github/jackofnonetrades/multi3ify-bootstrap/{release.stem}/"
                        f"multi3ify-bootstrap-{release.stem}.jar")
        path = output / release_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(release_data)
        if release.stem == version:
            relative = release_path
    return {"name": f"io.github.jackofnonetrades:multi3ify-bootstrap:{version}",
            "downloads": {"artifact": {"url": site_url.rstrip("/") + "/" + relative,
                                        "sha1": hashlib.sha1(data).hexdigest(), "size": len(data)}}}


def lwjgl3ify_document(profile, latest_forge, bootstrap):
    patches = profile["patches"]
    forge = next(p for p in patches if p["uid"] == "net.minecraftforge")
    minecraft = next(p for p in patches if p["uid"] == "net.minecraft")
    # forgePatches replaces classes in this exact Forge build: never silently mix builds.
    if forge["version"] != latest_forge:
        raise ValueError(f"lwjgl3ify {profile['version']} targets Forge {forge['version']}, "
                         f"but upstream latest 1.7.10 Forge is {latest_forge}")
    if minecraft["version"] != "1.7.10" or any(j < 17 for j in minecraft["compatibleJavaMajors"]):
        raise ValueError("Unexpected upstream Minecraft/Java compatibility")
    supported = {"formatVersion", "uid", "version", "name", "order", "releaseTime", "type", "volatile",
                 "requires", "conflicts", "libraries", "+jvmArgs", "+tweakers", "+traits", "mainClass",
                 "compatibleJavaMajors", "compatibleJavaName", "mainJar", "minecraftArguments", "assetIndex"}
    for patch in patches:
        if set(patch) - supported:
            raise ValueError(f"Unrecognized upstream patch fields: {set(patch) - supported}")
    document = {"formatVersion": 1, "uid": LWJGL3IFY_UID,
                "version": profile["version"],
                "name": "lwjgl3ify", "order": 5, "volatile": True,
                "releaseTime": profile["releaseTime"], "type": "release",
                "requires": [{"uid": "net.minecraft", "equals": GAME_VERSION}],
                "mainClass": BOOTSTRAP_CLASS, "libraries": [], "+jvmArgs": [], "+tweakers": [], "+traits": [],
                "compatibleJavaMajors": minecraft["compatibleJavaMajors"],
                "compatibleJavaName": minecraft["compatibleJavaName"]}
    delegate = None
    for patch in patches:
        for key in ("libraries", "+jvmArgs", "+tweakers", "+traits"):
            document[key].extend(copy.deepcopy(patch.get(key, [])))
        delegate = patch.get("mainClass", delegate)
    if not delegate or not delegate.startswith("com.gtnewhorizons.retrofuturabootstrap."):
        raise ValueError("Unexpected upstream entry point")
    if any(lib.get("MMC-hint") == "local" for lib in document["libraries"]):
        raise ValueError("Unresolved local library in upstream profile")
    document["libraries"].append(copy.deepcopy(bootstrap))
    document["+jvmArgs"].extend([f"-Dmulti3ify.modUrl={profile['modUrl']}",
                                 f"-Dmulti3ify.modSha256={profile['modSha256']}",
                                 f"-Dmulti3ify.delegate={delegate}"])
    return document


def forge_bridge(latest_forge, release_time):
    # Reuse the published latest ID: existing instances acquire the independent
    # component on refresh. No libraries here, so dependency insertion order cannot
    # put vanilla Forge ahead of the early lwjgl3ify Forge patches.
    return {"formatVersion": 1, "uid": "net.minecraftforge",
            "version": f"{latest_forge}-lwjgl3ify-latest", "name": "Forge (lwjgl3ify)",
            "order": 5, "releaseTime": release_time, "type": "release",
            "requires": [{"uid": "net.minecraft", "equals": GAME_VERSION},
                         {"uid": LWJGL3IFY_UID, "suggests": "latest"}]}


def restore_legacy_forge(meta):
    """Keep old pinned URLs byte-identical, outside the new selection index.

    Prism retains hashes of removed index entries in its cache. Rewriting these
    documents as redirects would break existing pinned instances with that cache.
    The archive also preserves releases excluded by a later minimum setting.
    """
    with zipfile.ZipFile(ROOT / "metadata/legacy-forge.zip") as archive:
        entries = json.loads(archive.read("index.json"))["versions"]
        for entry in entries:
            version = segment(entry["version"])
            data = archive.read(version + ".json")
            if sha256(data) != entry["sha256"]:
                raise ValueError(f"Legacy Forge checksum mismatch: {version}")
            target = meta / "net.minecraftforge" / (version + ".json")
            if target.exists():
                raise ValueError(f"Legacy Forge would overwrite upstream: {version}")
            target.write_bytes(data)
    return len(entries)


def add_versions(meta, uid, documents, *, aliases=None, name=None):
    index_path = meta / uid / "index.json"
    root_path = meta / "index.json"
    root = json.loads(root_path.read_bytes())
    package = next((p for p in root["packages"] if p["uid"] == uid), None)
    if package is None:
        if not name:
            raise ValueError(f"New metadata package needs a name: {uid}")
        package = {"uid": uid, "name": name}
        root["packages"].append(package)
        index = {"formatVersion": 1, "uid": uid, "name": name, "versions": []}
    else:
        index = json.loads(index_path.read_bytes())
    existing = {v["version"] for v in index["versions"]}
    entries = []
    for document in documents:
        version = segment((aliases or {}).get(document["version"], document["version"]))
        if document["version"] != reported_version(uid, version):
            raise ValueError(f"Unsupported version alias: {uid}/{version}")
        if version in existing:
            raise ValueError(f"Refusing to replace upstream version {uid}/{version}")
        existing.add(version)
        digest = write_json(meta / uid / f"{version}.json", document)
        entry = {k: document[k] for k in ("version", "releaseTime", "type", "requires", "volatile") if k in document}
        if uid in {LWJGL3IFY_UID, CLEANROOM_UID}:
            entry["recommended"] = version == "latest"
        entry["version"] = version
        entry["sha256"] = digest
        entries.append(entry)
    index["versions"] = entries + index["versions"]
    digest = write_json(index_path, index)
    package["sha256"] = digest
    write_json(root_path, root)


def build(source, output, site_url, profiles, cleanroom_profiles=None, *, fjord_source=None):
    if output.exists():
        raise ValueError(f"Output already exists; choose a fresh directory: {output}")
    output.mkdir(parents=True)
    meta = output / "v1"
    meta.mkdir()
    count = mirror(source, meta)
    print(f"Mirrored {count} upstream versions", flush=True)
    if fjord_source is not None:
        fjord_count = mirror(fjord_source, meta, packages=FJORD_PACKAGES)
        print(f"Mirrored {fjord_count} Fjord authentication agent versions", flush=True)
    index = json.loads((meta / "net.minecraftforge/index.json").read_bytes())
    forge_versions = [v["version"] for v in index["versions"]
                      if any(r.get("equals") == "1.7.10" and r["uid"] == "net.minecraft"
                             for r in v.get("requires", []))]
    latest_forge = max(forge_versions, key=lambda v: tuple(map(int, v.split("."))))
    bootstrap = build_bootstrap(output, site_url)
    documents = [lwjgl3ify_document(p, latest_forge, bootstrap) for p in profiles]
    latest = copy.deepcopy(documents[0])
    latest["version"] = "latest"
    documents.insert(0, latest)
    bridge = forge_bridge(latest_forge, profiles[0]["releaseTime"])
    minecraft = json.loads((meta / "net.minecraft/1.7.10.json").read_bytes())
    # All runtime libraries and Java requirements belong to the lwjgl3ify component.
    # This avoids mixing vanilla LWJGL 2/Java 8 with a modern lwjgl3ify release.
    for key in ("libraries", "compatibleJavaMajors", "compatibleJavaName", "mainClass"):
        minecraft.pop(key, None)
    minecraft.update({"version": GAME_VERSION, "name": "Minecraft 1.7.10 + lwjgl3ify",
                      "releaseTime": profiles[0]["releaseTime"],
                      "requires": [{"uid": "net.minecraftforge", "suggests": bridge["version"]}]})
    add_versions(meta, LWJGL3IFY_UID, documents, name="lwjgl3ify")
    add_versions(meta, "net.minecraftforge", [bridge])
    add_versions(meta, "net.minecraft", [minecraft], aliases={GAME_VERSION: MC_VERSION})
    legacy_count = restore_legacy_forge(meta)
    (output / ".nojekyll").touch()
    shutil.copyfile(ROOT / "site/index.html", output / "index.html")
    status = {"builtAt": datetime.now(timezone.utc).isoformat(),
               "upstreamVersions": count, "minecraft": MC_VERSION, "gameVersion": GAME_VERSION, "forge": latest_forge,
               "lwjgl3ifyComponent": LWJGL3IFY_UID, "forgeComponentVersion": bridge["version"],
               "legacyForgeVersions": legacy_count,
               "latest": profiles[0]["version"], "versions": [p["version"] for p in profiles]}
    if cleanroom_profiles is not None:
        from scripts import cleanroom
        status["cleanroom"] = cleanroom.publish(meta, cleanroom_profiles)
    write_json(output / "status.json", status)
    return documents


def main():
    from scripts import cleanroom
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, required=True, help="Checkout of PrismLauncher/meta-launcher")
    parser.add_argument("--fjord-upstream", type=Path, help="Checkout of unmojang/meta-launcher for Fjord authentication agents")
    parser.add_argument("--output", type=Path, default=Path("public"), help="Must not exist")
    parser.add_argument("--site-url", required=True, help="Public Pages root, without /v1")
    parser.add_argument("--cache", type=Path, default=Path(".cache/releases"))
    parser.add_argument("--minimum", default="3.0.0", help="Oldest stable lwjgl3ify release to include (3.x+)")
    parser.add_argument("--cleanroom-minimum", default=cleanroom.MINIMUM,
                        help="Oldest supported published Cleanroom release (including alpha releases)")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if version_key(args.minimum) < (3, 0, 0):
        parser.error("lwjgl3ify older than 3.0.0 uses an unsupported patch layout")
    if cleanroom.version_key(args.cleanroom_minimum) < cleanroom.version_key(cleanroom.MINIMUM):
        parser.error(f"Cleanroom older than {cleanroom.MINIMUM} uses an unverified patch layout")
    minecraft = json.loads((args.upstream / "net.minecraft" / f"{cleanroom.GAME_VERSION}.json").read_bytes())
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        profiles = list(pool.map(lambda r: release_profile(r, args.cache), releases(args.minimum)))
        cleanroom_profiles = list(pool.map(lambda r: cleanroom.release_profile(r, args.cache / "cleanroom", minecraft),
                                           cleanroom.releases(args.cleanroom_minimum)))
    documents = build(args.upstream, args.output, args.site_url, profiles, cleanroom_profiles,
                      fjord_source=args.fjord_upstream)
    print(f"Built {len(documents)} lwjgl3ify and {len(cleanroom_profiles) + 1} Cleanroom versions "
          f"(including latest) at {args.output}")


if __name__ == "__main__":
    # Keep direct script invocation working as well as python -m scripts.build_metadata.
    import sys
    sys.path.insert(0, str(ROOT))
    main()
