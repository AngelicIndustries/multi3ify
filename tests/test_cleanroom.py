import copy
import hashlib
import io
import json
from itertools import permutations
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from scripts import build_metadata as meta, cleanroom
from scripts.verify_metadata import verify
from scripts.smoke_cleanroom import library_tasks
from test_metadata import profile as lwjgl3ify_profile, upstream


UNIVERSAL = b"test universal jar"


def artifact(data=b"test library", url="https://example.org/library.jar"):
    return {"url": url, "sha1": hashlib.sha1(data).hexdigest(), "size": len(data)}


def profile(version=cleanroom.MINIMUM):
    library = {"name": "org.lwjgl:lwjgl:3.4.1-unsafe", "downloads": {"artifact": artifact()}}
    narrator = {"name": "com.mojang:text2speech:1.10.3", "downloads": {"artifact": artifact()},
                "rules": [{"action": "allow"}, {"action": "disallow", "os": {"name": "osx-arm64"}}]}
    native_narrator = copy.deepcopy(narrator)
    native_narrator["downloads"]["classifiers"] = {"natives-linux": artifact(url="https://example.org/natives.jar")}
    native_narrator["natives"] = {"linux": "natives-linux"}
    native_narrator["extract"] = {"exclude": ["META-INF/"]}
    return {"version": version, "releaseTime": "2026-09-12T13:25:19Z",
            "archiveSha256": "a" * 64, "universalSha256": meta.sha256(UNIVERSAL), "patches": [
        {"formatVersion": 1, "uid": "org.lwjgl3", "version": "3.4.1", "libraries": [library]},
        {"formatVersion": 1, "uid": "net.minecraft", "version": "1.12.2", "name": "Minecraft",
         "mainClass": "net.minecraft.client.main.Main", "compatibleJavaMajors": [25, 26],
         "requires": [{"uid": "org.lwjgl3", "suggests": "3.3.1"}], "libraries": [narrator, native_narrator],
         "mainJar": {"name": "com.mojang:minecraft:1.12.2:client", "downloads": {"artifact": artifact()}},
         "assetIndex": {"id": "1.12", **artifact()}, "minecraftArguments": "--version ${version_name}"},
        {"formatVersion": 1, "uid": "net.minecraftforge", "version": version, "name": "Cleanroom",
         "mainClass": cleanroom.MAIN_CLASS, "+tweakers": ["net.minecraftforge.fml.common.launcher.FMLTweaker"],
         "+jvmArgs": ["-Dfile.encoding=UTF-8"], "requires": [{"uid": "net.minecraft", "equals": "1.12.2"}],
         "libraries": [{"name": f"com.cleanroommc:cleanroom:{version}", "downloads": {"artifact": artifact(UNIVERSAL)}}]},
    ]}


def source(path):
    upstream(path)
    meta.add_versions(path, "net.minecraft", [{"formatVersion": 1, "uid": "net.minecraft", "version": "1.12.2"}])
    meta.add_versions(path, "net.minecraft.java", [{"formatVersion": 1, "uid": "net.minecraft.java", "version": "java25",
                      "runtimes": [{"name": "java-runtime-epsilon", "version": {"major": 25}, "runtimeOS": "linux-x64"}]}], name="Java")


def modern_profile():
    # Real 0.7.4 pack, including classifier-only natives and empty local jars:
    # https://github.com/CleanroomMC/Cleanroom/releases/tag/0.7.4-alpha
    with zipfile.ZipFile(Path(__file__).parent / "fixtures/cleanroom-0.7.4-alpha.zip") as archive:
        lwjgl = json.loads(archive.read("patches/org.lwjgl.json"))
        loader = json.loads(archive.read("patches/net.minecraftforge.json"))
    for library in loader["libraries"]:
        if library["name"].startswith("com.cleanroommc:cleanroom:"):
            library["downloads"]["artifact"] = artifact(UNIVERSAL)
    minecraft = profile()["patches"][1]
    minecraft.update(compatibleJavaMajors=[8], compatibleJavaName="jre-legacy",
                     requires=[{"uid": "org.lwjgl", "suggests": "2.9.4-nightly-20150209"}],
                     logging={"type": "log4j2-xml"})
    minecraft["libraries"] += [{"name": name, "downloads": {"artifact": artifact()}}
                               for name in ("com.mojang:patchy:1.3.9", "oshi-project:oshi-core:1.1",
                                            "com.google.guava:guava:21.0")]
    return {**profile(), "version": loader["version"], "patches": [minecraft, lwjgl, loader]}


def release_assets(p, mutate=None):
    buffer = io.BytesIO()
    modern = p["patches"][0]["uid"] == "net.minecraft"
    with zipfile.ZipFile(buffer, "w") as archive:
        for document in p["patches"]:
            if modern and document["uid"] == "net.minecraft":
                continue
            archive.writestr(f"patches/{document['uid']}.json", meta.encoded(document))
        if modern:
            empty_jar = io.BytesIO()
            with zipfile.ZipFile(empty_jar, "w"):
                pass
            for library in p["patches"][-1]["libraries"]:
                if library.get("MMC-hint") == "local":
                    _, name, version = library["name"].split(":")
                    archive.writestr(f"libraries/{name}-{version}.jar", empty_jar.getvalue())
        archive.writestr("mmc-pack.json", meta.encoded({"formatVersion": 1, "components": [
            {"uid": d["uid"], "version": d["version"]} for d in p["patches"]]}))
        if mutate:
            mutate(archive)
    data = [buffer.getvalue(), UNIVERSAL]
    assets = [{"name": f"cleanroom-{p['version']}{suffix}", "size": len(b), "digest": "sha256:" + meta.sha256(b),
               "browser_download_url": "https://example.org/" + suffix} for b, suffix in zip(data, (".zip", "-universal.jar"))]
    return {"tag_name": p["version"], "published_at": p["releaseTime"], "assets": assets}, data


class CleanroomTests(unittest.TestCase):
    def setUp(self):
        # CI fills the real lock after deployment. Synthetic releases must not
        # depend on which upstream artifacts this checkout has published.
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        lock = root / "cleanroom-lock.json"
        lock.write_text("{}\n")
        self.enterContext(patch.object(cleanroom, "LOCK_PATH", lock))

    def test_release_filter_accepts_published_alpha_and_orders_numerically(self):
        def release(tag, **kw):
            return {"tag_name": tag, "draft": False, "prerelease": False, **kw}
        first = [release("0.6.9-alpha")] * 96 + [release("0.6.14-alpha"), release("0.6.14"),
                   release("0.6.15-alpha", prerelease=True), release("0.6.16-alpha", draft=True)]
        second = [release("v0.6.13-alpha"), release("0.7.0-beta"), release("nightly")]
        with patch.object(cleanroom, "fetch", side_effect=[meta.encoded(first), meta.encoded(second)]):
            self.assertEqual([r["tag_name"] for r in cleanroom.releases(cleanroom.MINIMUM)],
                             ["0.6.14", "0.6.14-alpha", "v0.6.13-alpha"])

    def test_archive_verification_and_cache(self):
        release, data = release_assets(profile())
        with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", side_effect=data) as fetch:
            result = cleanroom.release_profile(release, Path(tmp))
            self.assertEqual(result["patches"], profile()["patches"])
            self.assertEqual(result, cleanroom.release_profile(release, Path(tmp)))
            self.assertEqual(fetch.call_count, 2)
        bad = copy.deepcopy(release)
        bad["assets"][0]["digest"] = "sha256:" + "0" * 64
        with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", return_value=data[0]):
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                cleanroom.release_profile(bad, Path(tmp))

    def test_modern_archive_inherits_minecraft_and_preserves_launch_settings(self):
        p = modern_profile()
        vanilla = copy.deepcopy(p["patches"][0])
        release, data = release_assets(p)
        with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", side_effect=data) as fetch:
            root = Path(tmp)
            result = cleanroom.release_profile(release, root / "cache", vanilla)
            self.assertEqual(result, cleanroom.release_profile(release, root / "cache", vanilla))
            self.assertEqual(fetch.call_count, 2)
            source(root / "meta")
            minecraft, _, runtime = cleanroom.documents(result, root / "meta")
        self.assertEqual(vanilla, p["patches"][0])
        self.assertEqual(runtime["compatibleJavaMajors"], [25])
        self.assertEqual(runtime["compatibleJavaName"], "java-runtime-epsilon")
        self.assertEqual(runtime["+traits"], ["FirstThreadOnMacOS"])
        self.assertEqual(runtime["mainClass"], cleanroom.MAIN_CLASS)
        self.assertEqual(minecraft["mainJar"], vanilla["mainJar"])
        self.assertEqual(minecraft["minecraftArguments"], vanilla["minecraftArguments"])
        self.assertNotIn("logging", minecraft)
        self.assertNotIn("libraries", minecraft)
        self.assertEqual(result["patches"][1], p["patches"][1])
        self.assertFalse(any(lib.get("MMC-hint") == "local" for lib in runtime["libraries"]))
        names = {lib["name"] for lib in runtime["libraries"]}
        self.assertNotIn("com.mojang:patchy:1.3.9", names)
        self.assertNotIn("oshi-project:oshi-core:1.1", names)
        self.assertIn("com.cleanroommc:cleanroom:0.7.4-alpha:universal", names)
        tasks, classpath, natives = library_tasks(runtime["libraries"], Path("test"))
        self.assertIn("guava-33.6.0-jre.jar", [p.name for p in classpath])
        self.assertNotIn("guava/21.0", str(classpath))
        self.assertEqual(len(natives), 8)  # Seven LWJGL modules plus narrator.
        self.assertEqual(len(tasks), len(classpath) + len(natives))

    def test_modern_cache_tracks_inherited_minecraft(self):
        p = modern_profile()
        release, data = release_assets(p)
        with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", side_effect=data * 2) as fetch:
            with self.assertRaisesRegex(ValueError, "needs upstream Minecraft"):
                cleanroom.release_profile(release, Path(tmp))
            first = cleanroom.release_profile(release, Path(tmp), p["patches"][0])
            p["patches"][0]["minecraftArguments"] += " --demo"
            second = cleanroom.release_profile(release, Path(tmp), p["patches"][0])
            self.assertNotEqual(first, second)
            self.assertEqual(fetch.call_count, 4)

    def test_modern_local_overrides_must_be_known_and_empty(self):
        p = modern_profile()
        for library in p["patches"][-1]["libraries"]:
            if library.get("MMC-hint") == "local":
                library["name"] = "unknown:library:999999.0-empty"
                break
        release, data = release_assets(p)
        with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", side_effect=data):
            with self.assertRaisesRegex(ValueError, "Unsupported local Cleanroom override"):
                cleanroom.release_profile(release, Path(tmp), p["patches"][0])
        release, data = release_assets(modern_profile())
        # Replace one empty jar with a real payload without duplicate ZIP names.
        jar = io.BytesIO()
        with zipfile.ZipFile(jar, "w") as archive:
            archive.writestr("Unexpected.class", b"payload")
        output = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(data[0])) as original, zipfile.ZipFile(output, "w") as changed:
            for name in original.namelist():
                changed.writestr(name, jar.getvalue() if name.endswith("patchy-999999.0-empty.jar") else original.read(name))
        data[0] = output.getvalue()
        release["assets"][0].update(size=len(data[0]), digest="sha256:" + meta.sha256(data[0]))
        with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", side_effect=data):
            with self.assertRaisesRegex(ValueError, "override is not empty"):
                cleanroom.release_profile(release, Path(tmp), modern_profile()["patches"][0])

    def test_native_only_entries_require_valid_classifiers(self):
        native = modern_profile()["patches"][1]["libraries"][1]
        cleanroom.validate_library(native)
        for change in (lambda lib: lib.pop("natives"),
                       lambda lib: lib["downloads"]["classifiers"].pop("natives-linux"),
                       lambda lib: lib["downloads"]["classifiers"]["natives-linux"].update(sha1="invalid")):
            with self.subTest(change=change):
                library = copy.deepcopy(native)
                change(library)
                with self.assertRaises(ValueError):
                    cleanroom.validate_library(library)

    def test_modern_pack_still_rejects_unrecognized_runtime_changes(self):
        for change in (
            lambda p: p["patches"][-1].update(compatibleJavaMajors=[8]),
            lambda p: p["patches"][-1]["requires"][1].update(equals="3.0.0"),
            lambda p: p["patches"][-1].update(mainClass="new.Launcher"),
            lambda p: p["patches"][1].update(newLaunchMechanism=True),
            lambda p: p["patches"][1].update(uid="org.unknown"),
        ):
            with self.subTest(change=change):
                p = modern_profile()
                change(p)
                release, data = release_assets(p)
                with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", side_effect=data):
                    with self.assertRaises(ValueError):
                        cleanroom.release_profile(release, Path(tmp), p["patches"][0])

    def test_missing_assets_changed_layout_and_wrong_universal_are_rejected(self):
        release, data = release_assets(profile(), lambda z: z.writestr("patches/new.json", "{}"))
        with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", side_effect=data):
            with self.assertRaisesRegex(ValueError, "archive layout"):
                cleanroom.release_profile(release, Path(tmp))
        release["assets"].pop()
        with self.assertRaisesRegex(ValueError, "missing required assets"):
            cleanroom.release_profile(release, Path("unused"))
        p = profile()
        p["patches"][-1]["libraries"][0]["downloads"]["artifact"]["sha1"] = "0" * 40
        release, data = release_assets(p)
        with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", side_effect=data):
            with self.assertRaisesRegex(ValueError, "universal jar and profile disagree"):
                cleanroom.release_profile(release, Path(tmp))

    def test_unrecognized_runtime_changes_fail_closed(self):
        for change in (
            lambda p: p["patches"][-1].update(mainClass="new.Launcher"),
            lambda p: p["patches"][1].update(compatibleJavaMajors=[8, 25]),
            lambda p: p["patches"][1].update(compatibleJavaMajors=[27]),
            lambda p: p["patches"][0].update(newLaunchMechanism=True),
            lambda p: p["patches"][0]["libraries"][0].update({"MMC-hint": "local"}),
            lambda p: p["patches"][0].update(version="3.5.0"),
        ):
            with self.subTest(change=change):
                p = profile()
                change(p)
                with self.assertRaises(ValueError):
                    cleanroom.validate_profile(p)

    def test_runtime_preserves_rules_natives_and_owns_all_launch_settings(self):
        p = profile()
        with tempfile.TemporaryDirectory() as tmp:
            source(Path(tmp))
            minecraft, bridge, runtime = cleanroom.documents(p, Path(tmp))
        self.assertEqual(p, profile())
        self.assertEqual(runtime["libraries"], [lib for patch in p["patches"] for lib in patch["libraries"]])
        self.assertEqual(minecraft["mainJar"], p["patches"][1]["mainJar"])
        self.assertEqual(minecraft["assetIndex"], p["patches"][1]["assetIndex"])
        self.assertEqual(minecraft["requires"], [{"uid": "net.minecraftforge", "equals": cleanroom.BRIDGE_VERSION}])
        self.assertNotEqual(bridge["name"], runtime["name"], "the release picker must be distinguishable from the bridge")
        for components in permutations([minecraft, bridge, runtime]):
            settings = {key: value for c in components for key, value in c.items()
                        if key in ("mainClass", "compatibleJavaMajors", "compatibleJavaName")}
            self.assertEqual(settings, {"mainClass": cleanroom.MAIN_CLASS, "compatibleJavaMajors": [25, 26],
                                        "compatibleJavaName": "java-runtime-epsilon"})
        self.assertEqual(runtime["requires"], [{"uid": "net.minecraft", "equals": "1.12.2"}])
        self.assertNotIn("mods", runtime)
        self.assertNotIn("multi3ify.", str(runtime))

    def test_java_download_runtime_must_exist_in_mirrored_feed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source(root)
            meta.write_json(root / "net.minecraft.java/java25.json", {"runtimes": []})
            with self.assertRaisesRegex(ValueError, "no matching Java runtime"):
                cleanroom.documents(profile(), root)

    def test_smoke_classpath_keeps_narrator_and_extracts_its_native(self):
        libraries = profile()["patches"][1]["libraries"]
        tasks, classpath, natives = library_tasks(libraries + libraries, Path("test"))
        self.assertEqual(len(tasks), 2)
        self.assertEqual(len(classpath), 1)
        self.assertEqual(len(natives), 1)
        self.assertEqual(classpath[0].name, "library.jar")
        self.assertEqual(natives[0].name, "natives.jar")

    def test_smoke_resolves_numeric_upgrades_in_either_order(self):
        old = {"name": "test:library:9.0", "downloads": {"artifact": artifact(url="https://example.org/old.jar")}}
        new = {"name": "test:library:10.0-jre", "downloads": {"artifact": artifact(url="https://example.org/new.jar")}}
        for libraries in ([old, new], [new, old]):
            _, classpath, _ = library_tasks(libraries, Path("test"))
            self.assertEqual([p.name for p in classpath], ["new.jar"])
        ambiguous = {**old, "name": "test:library:9.0-other"}
        with self.assertRaisesRegex(ValueError, "Prism version comparison"):
            library_tasks([old, ambiguous], Path("test"))

    def test_modern_publication_keeps_legacy_pin_and_base_game(self):
        p = modern_profile()
        release, data = release_assets(p)
        with tempfile.TemporaryDirectory() as tmp, patch.object(meta, "fetch", side_effect=data):
            root = Path(tmp)
            modern = cleanroom.release_profile(release, root / "cache", p["patches"][0])
            source(root / "upstream")
            with patch.object(meta, "build_bootstrap", return_value={"name": "test:bootstrap:1"}):
                meta.build(root / "upstream", root / "before", "https://example.org", [lwjgl3ify_profile()], [profile()])
                with patch.object(cleanroom, "LOCK_PATH", root / "before/cleanroom-lock.json"):
                    meta.build(root / "upstream", root / "after", "https://example.org", [lwjgl3ify_profile()], [modern, profile()])
            pin = f"v1/{meta.CLEANROOM_UID}/{cleanroom.MINIMUM}.json"
            self.assertEqual((root / "before" / pin).read_bytes(), (root / "after" / pin).read_bytes())
            verify(root / "after/v1", require_cleanroom=True)

    def test_combined_feed_preserves_existing_integration_and_pins(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source(root / "upstream")
            with patch.object(meta, "build_bootstrap", return_value={"name": "test:bootstrap:1"}):
                meta.build(root / "upstream", root / "old", "https://example.org", [lwjgl3ify_profile()])
                meta.build(root / "upstream", root / "before", "https://example.org", [lwjgl3ify_profile()], [profile()])
                meta.build(root / "upstream", root / "after", "https://example.org", [lwjgl3ify_profile()],
                           [profile("0.6.14-alpha"), profile()])
            for original in (root / "old/v1").rglob("*.json"):
                if original.name != "index.json":
                    self.assertEqual(original.read_bytes(), (root / "after/v1" / original.relative_to(root / "old/v1")).read_bytes())
            runtime_path = f"v1/{meta.CLEANROOM_UID}/{cleanroom.MINIMUM}.json"
            self.assertEqual((root / "before" / runtime_path).read_bytes(), (root / "after" / runtime_path).read_bytes())
            verify(root / "after/v1", require_cleanroom=True)
            mc = json.loads((root / "after/v1/net.minecraft/1.12.2-cleanroom.json").read_bytes())
            self.assertEqual(mc["version"], "1.12.2")
            index = json.loads((root / "after/v1" / meta.CLEANROOM_UID / "index.json").read_bytes())
            self.assertEqual([v["version"] for v in index["versions"] if v["recommended"]], ["latest"])
            with self.assertRaisesRegex(ValueError, "Missing custom Minecraft/Cleanroom"):
                verify(root / "old/v1", require_cleanroom=True)

    def test_published_release_cannot_be_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source(root / "v1")
            cleanroom.publish(root / "v1", [profile()])
            source(root / "second")
            p = profile()
            p["archiveSha256"] = "b" * 64
            with patch.object(cleanroom, "LOCK_PATH", root / "cleanroom-lock.json"):
                with self.assertRaisesRegex(ValueError, "Refusing to change published"):
                    cleanroom.publish(root / "second", [p])


if __name__ == "__main__":
    unittest.main()
