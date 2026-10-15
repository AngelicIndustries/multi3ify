"""Adapt published Cleanroom instance profiles without managing companion mods."""
import copy
import hashlib
import io
import json
import re
import zipfile

from scripts.build_metadata import (CLEANROOM_UID, ROOT, add_versions, asset_bytes,
                                    encoded, fetch, sha256, write_json)

MINIMUM = "0.6.13-alpha"
MC_VERSION = "1.12.2-cleanroom"
GAME_VERSION = "1.12.2"
BRIDGE_VERSION = "1.12.2-cleanroom-latest"
MAIN_CLASS = "top.outlands.foundation.boot.Foundation"
RELEASES_API = "https://api.github.com/repos/CleanroomMC/Cleanroom/releases"
CACHE_SCHEMA = 2
LOCK_PATH = ROOT / "metadata/cleanroom-lock.json"
JAVA_NAMES = {25: "java-runtime-epsilon"}
LWJGL2_LIBRARIES = {
    "net.java.jinput:jinput", "net.java.jinput:jinput-platform", "net.java.jutils:jutils",
    "org.lwjgl.lwjgl:lwjgl", "org.lwjgl.lwjgl:lwjgl-platform", "org.lwjgl.lwjgl:lwjgl_util",
}
PATCH_FIELDS = {
    "formatVersion", "uid", "version", "name", "order", "releaseTime", "type", "volatile",
    "requires", "libraries", "+jvmArgs", "+tweakers", "+traits", "mainClass",
    "compatibleJavaMajors", "compatibleJavaName", "mainJar", "minecraftArguments", "assetIndex",
}


def version_key(version):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(-alpha)?", version)
    if not match:
        raise ValueError(f"Unsupported Cleanroom version: {version}")
    return (*map(int, match.groups()[:3]), not bool(match[4]))


def releases(minimum):
    floor = version_key(minimum)
    result = []
    page = 1
    while True:
        batch = json.loads(fetch(f"{RELEASES_API}?per_page=100&page={page}"))
        if not isinstance(batch, list):
            raise ValueError("Unexpected Cleanroom releases response")
        for release in batch:
            version = release["tag_name"].removeprefix("v")
            if release["draft"] or release["prerelease"]:
                continue
            if not re.fullmatch(r"\d+\.\d+\.\d+(-alpha)?", version):
                continue
            if version_key(version) >= floor:
                result.append(release)
        if len(batch) < 100:
            break
        page += 1
    if not result:
        raise ValueError("No supported published Cleanroom releases found")
    return sorted(result, key=lambda r: version_key(r["tag_name"].removeprefix("v")), reverse=True)


def validate_download(artifact):
    if (not artifact.get("url", "").startswith("https://")
            or not re.fullmatch(r"[a-f0-9]{40}", artifact.get("sha1", ""))
            or not isinstance(artifact.get("size"), int) or artifact["size"] <= 0):
        raise ValueError(f"Incomplete Cleanroom artifact: {artifact}")


def validate_library(library):
    if library.get("MMC-hint") == "local" or set(library) - {"name", "downloads", "rules", "natives", "extract"}:
        raise ValueError("Unsupported/local Cleanroom library")
    downloads = library["downloads"]
    if set(downloads) - {"artifact", "classifiers"}:
        raise ValueError("Unknown Cleanroom download kind")
    # Cleanroom 0.7 splits ordinary jars and classifier-only native entries.
    if "artifact" in downloads:
        validate_download(downloads["artifact"])
    elif not library.get("natives"):
        raise ValueError("Cleanroom library has no artifact or natives")
    for artifact in downloads.get("classifiers", {}).values():
        validate_download(artifact)
    if any(c not in downloads.get("classifiers", {}) for c in library.get("natives", {}).values()):
        raise ValueError("Unresolved Cleanroom native classifier")


def components(profile):
    """Return LWJGL, Minecraft, loader, and the component owning Java settings."""
    patches = profile["patches"]
    uids = [p["uid"] for p in patches]
    if uids == ["org.lwjgl3", "net.minecraft", "net.minecraftforge"]:
        lwjgl, minecraft, loader = patches
        return lwjgl, minecraft, loader, minecraft
    if uids == ["net.minecraft", "org.lwjgl", "net.minecraftforge"]:
        minecraft, lwjgl, loader = patches
        return lwjgl, minecraft, loader, loader
    raise ValueError("Unexpected Cleanroom component layout/order")


def validate_profile(profile):
    patches = profile["patches"]
    lwjgl, minecraft, loader, java = components(profile)
    modern = lwjgl["uid"] == "org.lwjgl"
    for patch in patches:
        fields = PATCH_FIELDS | ({"logging"} if modern and patch is minecraft else set())
        if patch.get("formatVersion") != 1 or set(patch) - fields:
            raise ValueError(f"Unrecognized Cleanroom patch fields/format: {patch['uid']}")
        for library in patch["libraries"]:
            validate_library(library)
    if minecraft["version"] != GAME_VERSION or loader["version"] != profile["version"]:
        raise ValueError("Unexpected Cleanroom Minecraft/loader version")
    requirements = [{"uid": "net.minecraft", "equals": GAME_VERSION}]
    if modern:
        requirements.append({"uid": "org.lwjgl", "equals": lwjgl["version"]})
    if loader["requires"] != requirements:
        raise ValueError("Unexpected Cleanroom loader requirements")
    if (len(minecraft["requires"]) != 1 or minecraft["requires"][0]["uid"] != lwjgl["uid"]
            or lwjgl.get("requires")):
        raise ValueError("Unexpected Cleanroom runtime requirements")
    if loader["mainClass"] != MAIN_CLASS or loader["+tweakers"] != ["net.minecraftforge.fml.common.launcher.FMLTweaker"]:
        raise ValueError("Unsupported Cleanroom entry point")
    majors = java["compatibleJavaMajors"]
    if not majors or any(type(j) is not int or j < 25 for j in majors) or majors[0] not in JAVA_NAMES:
        raise ValueError("Unsupported Cleanroom Java compatibility")
    for patch in (lwjgl, loader):
        fields = {"mainJar", "assetIndex", "minecraftArguments"}
        if patch is not java:
            fields |= {"compatibleJavaMajors", "compatibleJavaName"}
        if fields & patch.keys():
            raise ValueError("Cleanroom moved game/Java fields between components")
    validate_download(minecraft["mainJar"]["downloads"]["artifact"])
    validate_download(minecraft["assetIndex"])
    libraries = [lib for patch in patches for lib in patch["libraries"]]
    if any(lib["name"].startswith(("net.minecraftforge:forge:", "net.minecraft:launchwrapper:",
                                   "org.lwjgl.lwjgl:", "org.ow2.asm:asm-all:")) for lib in libraries):
        raise ValueError("Obsolete runtime in Cleanroom profile")
    lwjgl_libraries = [lib for lib in libraries if lib["name"].startswith("org.lwjgl:")]
    # Upstream supplies a patched core module named <version>-unsafe.
    if not lwjgl_libraries or any(
        lib["name"].split(":")[2] != lwjgl["version"]
        and lib["name"] != f"org.lwjgl:lwjgl:{lwjgl['version']}-unsafe" for lib in lwjgl_libraries
    ):
        raise ValueError("Mismatched Cleanroom LWJGL versions")
    jars = [lib for lib in libraries if lib["name"].startswith("com.cleanroommc:cleanroom:")]
    jar_name = f"com.cleanroommc:cleanroom:{profile['version']}" + (":universal" if modern else "")
    if len(jars) != 1 or jars[0]["name"] != jar_name:
        raise ValueError("Unexpected Cleanroom universal jar")
    return jars[0]["downloads"]["artifact"]


def remove_empty_overrides(archive, minecraft, loader):
    """Replace upstream's empty local jars by omitting the libraries they disable.

    Our Minecraft alias has no independent runtime, so it needs neither the
    placeholder jars nor an LWJGL 2 component to override.
    """
    # Upstream may disable additional vanilla libraries in later releases.
    # Accept targets inherited from Minecraft, plus its omitted LWJGL 2 package.
    allowed = LWJGL2_LIBRARIES | {":".join(lib["name"].split(":")[:2]) for lib in minecraft["libraries"]}
    suppressed = set()
    members = set()
    for library in loader["libraries"]:
        if library.get("MMC-hint") != "local":
            continue
        parts = library["name"].split(":")
        coordinate = ":".join(parts[:2])
        if (set(library) != {"name", "MMC-hint"} or len(parts) != 3
                or coordinate not in allowed or parts[2] != "999999.0-empty"):
            raise ValueError(f"Unsupported local Cleanroom override: {library}")
        member = f"libraries/{parts[1]}-{parts[2]}.jar"
        with zipfile.ZipFile(io.BytesIO(archive.read(member))) as jar:
            if jar.namelist():
                raise ValueError(f"Cleanroom override is not empty: {member}")
        members.add(member)
        suppressed.add(coordinate)
    if {n for n in archive.namelist() if n.endswith(".jar")} != members:
        raise ValueError("Unexpected bundled Cleanroom jars")
    loader["libraries"] = [lib for lib in loader["libraries"] if lib.get("MMC-hint") != "local"]
    minecraft["libraries"] = [lib for lib in minecraft["libraries"]
                              if ":".join(lib["name"].split(":")[:2]) not in suppressed]


def release_profile(release, cache, minecraft=None):
    version = release["tag_name"].removeprefix("v")
    version_key(version)
    names = {a["name"]: a for a in release["assets"]}
    required = [f"cleanroom-{version}{suffix}" for suffix in (".zip", "-universal.jar")]
    if any(name not in names for name in required):
        raise ValueError(f"Cleanroom {version} is missing required assets")
    assets = [names[name] for name in required]
    fingerprints = [{k: a.get(k) for k in ("id", "name", "size", "updated_at", "digest", "browser_download_url")}
                    for a in assets]
    inputs = {"schema": CACHE_SCHEMA, "assets": fingerprints, "releaseTime": release["published_at"]}
    if version_key(version) >= version_key("0.7.0-alpha"):
        # New packs inherit vanilla Minecraft instead of shipping its patch.
        # Include that input in the cache key; publication still enforces locks.
        if minecraft is None:
            raise ValueError(f"Cleanroom {version} needs upstream Minecraft metadata")
        inputs["minecraft"] = minecraft
    key = sha256(encoded(inputs))
    cached = cache / f"{version}-{key}.json"
    if cached.exists():
        result = json.loads(cached.read_bytes())
    else:
        archive_data = asset_bytes(assets[0])
        with zipfile.ZipFile(io.BytesIO(archive_data)) as archive:
            modern = "minecraft" in inputs
            uids = (["net.minecraft", "org.lwjgl", "net.minecraftforge"] if modern
                    else ["org.lwjgl3", "net.minecraft", "net.minecraftforge"])
            expected = {f"patches/{uid}.json" for uid in uids if not (modern and uid == "net.minecraft")}
            members = [n for n in archive.namelist() if n.endswith(".json")]
            if len(members) != len(expected) + 1 or set(members) != expected | {"mmc-pack.json"}:
                raise ValueError(f"Unexpected Cleanroom archive layout: {members}")
            pack = json.loads(archive.read("mmc-pack.json"))
            entries = pack["components"]
            if pack.get("formatVersion") != 1 or [c["uid"] for c in entries] != uids:
                raise ValueError("Unexpected Cleanroom component layout/order")
            patches = [copy.deepcopy(minecraft) if modern and uid == "net.minecraft"
                       else json.loads(archive.read(f"patches/{uid}.json")) for uid in uids]
            if any(c["uid"] != p["uid"] or c["version"] != p["version"] for c, p in zip(entries, patches)):
                raise ValueError("Cleanroom pack and patch versions disagree")
            if modern:
                remove_empty_overrides(archive, patches[0], patches[2])
        result = {"version": version, "releaseTime": release["published_at"], "patches": patches,
                  "archiveSha256": sha256(archive_data)}
        artifact = validate_profile(result)
        universal = asset_bytes(assets[1])
        if len(universal) != artifact["size"] or hashlib.sha1(universal).hexdigest() != artifact["sha1"]:
            raise ValueError("Cleanroom universal jar and profile disagree")
        result["universalSha256"] = sha256(universal)
        write_json(cached, result)
        print(f"Prepared Cleanroom {version}", flush=True)
    validate_profile(result)
    return result


def documents(profile, meta):
    validate_profile(profile)
    _, game, _, java_settings = components(profile)
    minecraft = copy.deepcopy(game)
    majors = java_settings["compatibleJavaMajors"]
    java_name = JAVA_NAMES[majors[0]]
    java = json.loads((meta / "net.minecraft.java" / f"java{majors[0]}.json").read_bytes())
    if not any(r["name"] == java_name and r["version"]["major"] == majors[0] for r in java["runtimes"]):
        raise ValueError(f"Prism metadata has no matching Java runtime: {java_name}")
    runtime = {"formatVersion": 1, "uid": CLEANROOM_UID, "version": profile["version"],
               "name": "Cleanroom", "order": 5, "volatile": True, "type": "release",
               "releaseTime": profile["releaseTime"],
               "requires": [{"uid": "net.minecraft", "equals": GAME_VERSION}],
               "compatibleJavaMajors": majors, "compatibleJavaName": java_name}
    for field in ("libraries", "+jvmArgs", "+tweakers", "+traits"):
        runtime[field] = [copy.deepcopy(value) for patch in profile["patches"] for value in patch.get(field, [])]
    runtime["mainClass"] = MAIN_CLASS
    # The vanilla logging configuration targets its old Log4j runtime. Like
    # the 0.6 Minecraft patch, leave logging to Cleanroom's own configuration.
    for field in ("libraries", "logging", "mainClass", "compatibleJavaMajors", "compatibleJavaName", "+jvmArgs", "+tweakers", "+traits"):
        minecraft.pop(field, None)
    minecraft.update({"name": "Minecraft 1.12.2 + Cleanroom", "order": -2,
                      "releaseTime": profile["releaseTime"],
                      "requires": [{"uid": "net.minecraftforge", "equals": BRIDGE_VERSION}]})
    bridge = {"formatVersion": 1, "uid": "net.minecraftforge", "version": BRIDGE_VERSION,
              "name": "Forge (Cleanroom)", "order": 5, "type": "release", "releaseTime": profile["releaseTime"],
              "requires": [{"uid": "net.minecraft", "equals": GAME_VERSION},
                           {"uid": CLEANROOM_UID, "suggests": "latest"}]}
    return minecraft, bridge, runtime


def publish(meta, profiles):
    if not profiles:
        raise ValueError("Cleanroom publication needs at least one release")
    locked = json.loads(LOCK_PATH.read_bytes())
    published = dict(locked)
    versions = []
    base = None
    for profile in profiles:
        minecraft, bridge, runtime = documents(profile, meta)
        # Assets/game arguments belong to Minecraft. Refuse a release that would
        # change these for users pinned to an earlier Cleanroom runtime.
        game = {k: v for k, v in minecraft.items() if k != "releaseTime"}
        if base is not None and game != base:
            raise ValueError("Cleanroom releases disagree on base Minecraft metadata")
        base = game
        fingerprint = {k: profile[k] for k in ("archiveSha256", "universalSha256")}
        fingerprint["documentSha256"] = sha256(encoded(runtime))
        version = profile["version"]
        if version in locked and locked[version] != fingerprint:
            raise ValueError(f"Refusing to change published Cleanroom {version}")
        published[version] = fingerprint
        versions.append(runtime)
    latest = copy.deepcopy(versions[0])
    latest["version"] = "latest"
    minecraft, bridge, _ = documents(profiles[0], meta)
    add_versions(meta, CLEANROOM_UID, [latest, *versions], name="Cleanroom")
    add_versions(meta, "net.minecraftforge", [bridge])
    add_versions(meta, "net.minecraft", [minecraft], aliases={GAME_VERSION: MC_VERSION})
    write_json(meta.parent / "cleanroom-lock.json", published)
    return {"minecraft": MC_VERSION, "gameVersion": GAME_VERSION, "component": CLEANROOM_UID,
            "forgeComponentVersion": BRIDGE_VERSION, "latest": profiles[0]["version"],
            "versions": [p["version"] for p in profiles]}
