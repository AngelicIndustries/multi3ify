from itertools import permutations
import zipfile
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import build_metadata as meta
from scripts.verify_metadata import verify


def profile(version="3.0.33"):
    return {"version": version, "releaseTime": "2026-09-07T16:21:21Z",
            "modUrl": f"https://example.org/lwjgl3ify-{version}.jar", "modSha256": "a" * 64,
            "patches": [
                {"uid": "net.minecraft", "version": "1.7.10", "order": -2,
                 "compatibleJavaMajors": [17, 21], "compatibleJavaName": "java-runtime-delta",
                 "libraries": [{"name": "com.mojang:netty:1.8.8"}], "mainClass": "vanilla.Main"},
                {"uid": "org.lwjgl3", "order": -1, "libraries": [{"name": "org.lwjgl:lwjgl:3.3.3"}]},
                {"uid": "me.eigenraven.lwjgl3ify.forgepatches", "order": 3,
                 "libraries": [{"name": "com.github.GTNewHorizons:lwjgl3ify:3.0.33:forgePatches"}],
                 "+jvmArgs": ["--add-opens", "java.base/java.lang=ALL-UNNAMED"]},
                {"uid": "net.minecraftforge", "version": "10.13.4.1614", "order": 5,
                 "+tweakers": ["cpw.mods.fml.common.launcher.FMLTweaker"],
                 "libraries": [{"name": "net.minecraftforge:forge:1.7.10-10.13.4.1614-1.7.10:universal"}]},
                {"uid": "me.eigenraven.lwjgl3ify.launchargs", "order": 100,
                 "mainClass": "com.gtnewhorizons.retrofuturabootstrap.MainStartOnFirstThread",
                 "+traits": ["FirstThreadOnMacOS"]},
            ]}


def upstream(path):
    packages = []
    for uid, documents in {
        "net.minecraft": [{"uid": "net.minecraft", "version": "1.7.10", "formatVersion": 1,
                           "releaseTime": "2014-05-14T17:29:23+00:00", "type": "release",
                           "mainClass": "vanilla.Main", "compatibleJavaMajors": [8],
                           "compatibleJavaName": "jre-legacy", "libraries": [],
                           "requires": [{"uid": "org.lwjgl", "suggests": "2.9.4"}],
                           "mainJar": {"name": "com.mojang:minecraft:1.7.10:client"}}],
        "net.minecraftforge": [{"uid": "net.minecraftforge", "version": "10.13.4.1614", "formatVersion": 1,
                                "requires": [{"uid": "net.minecraft", "equals": "1.7.10"}]}],
        "org.lwjgl": [{"uid": "org.lwjgl", "version": "2.9.4", "formatVersion": 1}],
    }.items():
        versions = []
        for document in documents:
            digest = meta.write_json(path / uid / (document["version"] + ".json"), document)
            entry = {"version": document["version"], "sha256": digest}
            if "requires" in document:
                entry["requires"] = document["requires"]
            versions.append(entry)
        digest = meta.write_json(path / uid / "index.json",
                                 {"formatVersion": 1, "uid": uid, "versions": versions})
        packages.append({"uid": uid, "sha256": digest})
    meta.write_json(path / "index.json", {"formatVersion": 1, "packages": packages})


class MetadataTests(unittest.TestCase):
    def test_release_filter_pagination_and_numeric_order(self):
        def release(tag, **extra):
            return {"tag_name": tag, "draft": False, "prerelease": False, **extra}
        first = [release("3.0.9"), release("3.0.10"), release("3.0.11-beta.1"),
                 release("3.0.12", prerelease=True), release("3.0.13", draft=True)]
        first.extend(release("2.1.18") for _ in range(95))
        with patch.object(meta, "fetch", side_effect=[meta.encoded(first), meta.encoded([release("3.0.33")])]) as fetch:
            result = meta.releases("3.0.0")
        self.assertEqual([r["tag_name"] for r in result], ["3.0.33", "3.0.10", "3.0.9"])
        self.assertIn("page=2", fetch.call_args.args[0])

    def test_complete_mirror_preserves_bytes_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, dest = Path(tmp) / "upstream", Path(tmp) / "mirror"
            upstream(source)
            self.assertEqual(meta.mirror(source, dest), 3)
            for file in source.rglob("*.json"):
                self.assertEqual(file.read_bytes(), (dest / file.relative_to(source)).read_bytes())
            (source / "net.minecraft/1.7.10.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                meta.mirror(source, dest)

    def test_fjord_agents_preserve_prism_packages_and_recommendations(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, fjord, output = (Path(tmp) / name for name in ("prism", "fjord", "public"))
            upstream(source)
            upstream(fjord)
            for uid in meta.FJORD_PACKAGES:
                meta.add_versions(fjord, uid, [{"uid": uid, "version": "1.2.8", "formatVersion": 1,
                                               "+agents": [{"name": "example:agent:1.2.8"}]}], name=uid)
                index_path = fjord / uid / "index.json"
                index = json.loads(index_path.read_bytes())
                index["versions"][0]["recommended"] = True
                digest = meta.write_json(index_path, index)
                root = json.loads((fjord / "index.json").read_bytes())
                next(p for p in root["packages"] if p["uid"] == uid)["sha256"] = digest
                meta.write_json(fjord / "index.json", root)
            # Unselected Fjord packages must never replace the Prism snapshot.
            (fjord / "net.minecraft/1.7.10.json").write_text("not valid metadata")
            with patch.object(meta, "build_bootstrap", return_value={"name": "test:bootstrap:1"}):
                meta.build(source, output, "https://example.org", [profile()], fjord_source=fjord)
            target = output / "v1"
            self.assertEqual((target / "net.minecraft/1.7.10.json").read_bytes(),
                             (source / "net.minecraft/1.7.10.json").read_bytes())
            for uid in meta.FJORD_PACKAGES:
                for file in (fjord / uid).glob("*.json"):
                    self.assertEqual(file.read_bytes(), (target / uid / file.name).read_bytes())
            verify(target)
            with self.assertRaisesRegex(ValueError, "overwrite"):
                meta.mirror(fjord, target, packages=meta.FJORD_PACKAGES)
            (fjord / "moe.yushi.authlibinjector/1.2.8.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                meta.mirror(fjord, source, packages=meta.FJORD_PACKAGES)

    def test_missing_fjord_package_fails_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, target = Path(tmp) / "source", Path(tmp) / "target"
            upstream(source)
            upstream(target)
            with self.assertRaisesRegex(ValueError, "Missing supplemental"):
                meta.mirror(source, target, packages=meta.FJORD_PACKAGES)

    def test_profile_preserves_classpath_order_mac_traits_and_jvm_arguments(self):
        p = profile()
        result = meta.lwjgl3ify_document(p, "10.13.4.1614", {"name": "test:bootstrap:1"})
        names = [lib["name"] for lib in result["libraries"]]
        self.assertLess(next(i for i, n in enumerate(names) if n.endswith("forgePatches")),
                        next(i for i, n in enumerate(names) if n.startswith("net.minecraftforge:")))
        self.assertEqual(result["mainClass"], meta.BOOTSTRAP_CLASS)
        self.assertNotIn(8, result["compatibleJavaMajors"])
        self.assertEqual(result["+traits"], ["FirstThreadOnMacOS"])
        self.assertEqual(result["+jvmArgs"][:2], ["--add-opens", "java.base/java.lang=ALL-UNNAMED"])
        self.assertTrue(any("MainStartOnFirstThread" in a for a in result["+jvmArgs"]))
        self.assertEqual(p, profile(), "generation must not mutate the cached release")

    def test_incompatible_future_releases_fail_closed(self):
        p = profile()
        with self.assertRaisesRegex(ValueError, "targets Forge"):
            meta.lwjgl3ify_document(p, "10.13.4.9999", {})
        p["patches"][-1]["newLaunchMechanism"] = True
        with self.assertRaisesRegex(ValueError, "Unrecognized"):
            meta.lwjgl3ify_document(p, "10.13.4.1614", {})

    def test_overlay_preserves_original_versions_and_resolves_from_either_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "upstream", Path(tmp) / "public"
            upstream(source)
            with patch.object(meta, "build_bootstrap", return_value={"name": "test:bootstrap:1"}):
                docs = meta.build(source, output, "https://example.org", [profile(), profile("3.0.32")])
            self.assertEqual(docs[0]["version"], "latest")
            self.assertEqual(len(docs), 3)
            mirrored = Path(tmp) / "verify"
            self.assertEqual(meta.mirror(output / "v1", mirrored), 8)
            for original in source.rglob("*.json"):
                if original.name != "index.json":
                    self.assertEqual(original.read_bytes(), (output / "v1" / original.relative_to(source)).read_bytes())
            minecraft = json.loads((output / "v1/net.minecraft" / (meta.MC_VERSION + ".json")).read_bytes())
            bridge_id = minecraft["requires"][0]["suggests"]
            bridge = json.loads((output / "v1/net.minecraftforge" / (bridge_id + ".json")).read_bytes())
            self.assertEqual(bridge["requires"][1], {"uid": meta.LWJGL3IFY_UID, "suggests": "latest"})
            self.assertEqual(docs[0]["requires"][0]["equals"], meta.GAME_VERSION)
            # Prism inserts auto-installed dependencies before their parent component.
            for components in permutations([minecraft, bridge, docs[0]]):
                effective = {}
                for component in components:
                    for key in ("mainClass", "compatibleJavaMajors", "compatibleJavaName"):
                        if key in component:
                            effective[key] = component[key]
                self.assertEqual(effective["mainClass"], meta.BOOTSTRAP_CLASS)
                self.assertNotIn(8, effective["compatibleJavaMajors"])

    def test_mod_search_uses_real_version_without_changing_selected_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "upstream", Path(tmp) / "public"
            upstream(source)
            with patch.object(meta, "build_bootstrap", return_value={"name": "test:bootstrap:1"}):
                docs = meta.build(source, output, "https://example.org", [profile()])
            root = output / "v1"
            index = json.loads((root / "net.minecraft/index.json").read_bytes())
            entry = next(v for v in index["versions"] if v["version"] == meta.MC_VERSION)
            data, minecraft = meta.read_verified(root / "net.minecraft" / (entry["version"] + ".json"), entry["sha256"])
            self.assertEqual(entry["version"], "1.7.10-lwjgl3ify")
            self.assertEqual(minecraft["version"], "1.7.10")
            self.assertEqual(minecraft["name"], "Minecraft 1.7.10 + lwjgl3ify")
            # Prism keeps Component::m_version as the lookup ID, but fills
            # m_cachedVersion from VersionFile::version. ModFilterWidget and
            # the dependency resolver both use the latter via getVersion().
            component = {"version": entry["version"], "cachedVersion": minecraft["version"]}
            self.assertEqual(component["version"], meta.MC_VERSION)
            runtime_index = json.loads((root / meta.LWJGL3IFY_UID / "index.json").read_bytes())
            for runtime in docs:
                parent = runtime["requires"][0]["equals"]
                self.assertEqual(parent, component["cachedVersion"])
                entry = next(v for v in runtime_index["versions"] if v["version"] == runtime["version"])
                self.assertEqual(entry["requires"], runtime["requires"])
            self.assertNotIn("libraries", minecraft)
            self.assertNotIn("compatibleJavaMajors", minecraft)

    def test_picker_is_independent_and_old_pinned_urls_keep_their_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output = Path(tmp) / "upstream", Path(tmp) / "public"
            upstream(source)
            with patch.object(meta, "build_bootstrap", return_value={"name": "test:bootstrap:1"}):
                meta.build(source, output, "https://example.org", [profile(), profile("3.0.32")])
            root = output / "v1"
            forge_index = json.loads((root / "net.minecraftforge/index.json").read_bytes())
            self.assertEqual([v["version"] for v in forge_index["versions"]],
                             ["10.13.4.1614-lwjgl3ify-latest", "10.13.4.1614"])
            package = next(p for p in json.loads((root / "index.json").read_bytes())["packages"]
                           if p["uid"] == meta.LWJGL3IFY_UID)
            self.assertEqual(package["name"], "lwjgl3ify")
            _, index = meta.read_verified(root / meta.LWJGL3IFY_UID / "index.json", package["sha256"])
            self.assertEqual([v["version"] for v in index["versions"]], ["latest", "3.0.33", "3.0.32"])
            self.assertEqual([v["version"] for v in index["versions"] if v["recommended"]], ["latest"])
            with zipfile.ZipFile(meta.ROOT / "metadata/legacy-forge.zip") as archive:
                entries = json.loads(archive.read("index.json"))["versions"]
                self.assertEqual(len(entries), 34)
                for entry in entries:
                    filename = entry["version"] + ".json"
                    data, _ = meta.read_verified(root / "net.minecraftforge" / filename, entry["sha256"])
                    self.assertEqual(data, archive.read(filename))
            verify(root)

    def test_future_release_updates_latest_without_changing_pins_or_forge_picker(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, before, after = (Path(tmp) / name for name in ("upstream", "before", "after"))
            upstream(source)
            with patch.object(meta, "build_bootstrap", return_value={"name": "test:bootstrap:1"}):
                meta.build(source, before, "https://example.org", [profile(), profile("3.0.32")])
                meta.build(source, after, "https://example.org", [profile("3.0.34"), profile(), profile("3.0.32")])
            for version in ("3.0.33", "3.0.32"):
                path = Path("v1") / meta.LWJGL3IFY_UID / (version + ".json")
                self.assertEqual((before / path).read_bytes(), (after / path).read_bytes())
            latest = Path("v1") / meta.LWJGL3IFY_UID / "latest.json"
            self.assertNotEqual((before / latest).read_bytes(), (after / latest).read_bytes())
            for site in (before, after):
                index = json.loads((site / "v1/net.minecraftforge/index.json").read_bytes())
                self.assertEqual(len(index["versions"]), 2)
                minecraft = json.loads((site / "v1/net.minecraft" / (meta.MC_VERSION + ".json")).read_bytes())
                # Minecraft's Change version action updates an existing Forge to
                # this suggestion and removes its old org.lwjgl dependency.
                self.assertEqual(minecraft["requires"], [{"uid": "net.minecraftforge",
                                  "suggests": "10.13.4.1614-lwjgl3ify-latest"}])
                self.assertNotIn("mainClass", minecraft)
                self.assertNotIn("libraries", minecraft)

    def test_alias_support_does_not_allow_arbitrary_identity_mismatches(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            upstream(source)
            with self.assertRaisesRegex(ValueError, "Unsupported version alias"):
                meta.add_versions(source, "net.minecraft", [{"version": "1.7.10"}],
                                  aliases={"1.7.10": "arbitrary-profile"})

    def test_path_traversal_and_overwriting_upstream_are_rejected(self):
        for value in ("../test", "..", "a/b", "a\\b", "\x00"):
            with self.assertRaises(ValueError):
                meta.segment(value)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            upstream(source)
            with self.assertRaisesRegex(ValueError, "Refusing to replace"):
                meta.add_versions(source, "net.minecraft", [{"version": "1.7.10"}])


if __name__ == "__main__":
    unittest.main()
