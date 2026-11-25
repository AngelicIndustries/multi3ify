from pathlib import Path
import os
import subprocess
import tempfile
import unittest
import zipfile

from scripts.build_metadata import ROOT


class BootstrapTests(unittest.TestCase):
    def test_lwjgl2_conflict_stops_before_mod_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            classes = root / "classes"
            classes.mkdir()
            subprocess.run(["javac", "--release", "8", "-Xlint:-options", "-d", str(classes),
                            str(ROOT / "bootstrap/src/io/github/jackofnonetrades/multi3ify/Bootstrap.java")], check=True)
            legacy = root / "legacy.jar"
            with zipfile.ZipFile(legacy, "w") as jar:
                # Resource lookup must not define or initialize the class: even
                # this non-classfile marker must yield the actionable diagnosis.
                jar.writestr("org/lwjgl/LWJGLException.class", b"LWJGL 2 marker")
            mods = root / "game/mods"
            mods.mkdir(parents=True)
            existing = mods / "lwjgl3ify-multi3ify-managed.jar"
            existing.write_bytes(b"preserve the existing mod")
            result = subprocess.run(["java", "-cp", os.pathsep.join([str(classes), str(legacy)]),
                                     "io.github.jackofnonetrades.multi3ify.Bootstrap",
                                     "--gameDir", str(mods.parent)], text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Minecraft -> Change version -> 1.7.10-lwjgl3ify", result.stderr)
            self.assertIn("Conflicting LWJGL 2 resource:", result.stderr)
            self.assertNotIn("VerifyError", result.stderr)
            self.assertNotIn("Missing metadata property", result.stderr)
            self.assertEqual(existing.read_bytes(), b"preserve the existing mod")
            self.assertEqual(list(mods.iterdir()), [existing])
            self.assertFalse((mods.parent / ".multi3ify.lock").exists())

    def test_liteloader_mistakes_stop_before_mod_installation(self):
        cases = {
            # Stock LiteLoader was installed before the instance moved to lwjgl3ify.
            "com/mumfrey/liteloader/launch/LiteLoaderTweaker.class": ([], "-> 1.7.10_04-lwjgl3ify"),
            # The lwjgl3ify LiteLoader was picked on an ordinary Forge instance.
            "org/lwjgl/LWJGLException.class": (["-Dmulti3ify.liteloaderSha256=" + "a" * 64],
                                               "Minecraft -> Change version -> 1.7.10-lwjgl3ify-liteloader.\n"),
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            classes = root / "classes"
            classes.mkdir()
            subprocess.run(["javac", "--release", "8", "-Xlint:-options", "-d", str(classes),
                            str(ROOT / "bootstrap/src/io/github/jackofnonetrades/multi3ify/Bootstrap.java")], check=True)
            for resource, (properties, expected) in cases.items():
                with self.subTest(resource=resource):
                    marker = root / (Path(resource).stem + ".jar")
                    with zipfile.ZipFile(marker, "w") as jar:
                        jar.writestr(resource, b"marker")
                    game = root / Path(resource).stem
                    result = subprocess.run(["java", *properties, "-cp", os.pathsep.join([str(classes), str(marker)]),
                                             "io.github.jackofnonetrades.multi3ify.Bootstrap", "--gameDir", str(game)],
                                            text=True, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)
                    self.assertIn("No mods were changed by this launch", result.stderr)
                    self.assertFalse(game.exists())

    def test_real_java_install_upgrade_rollback_and_offline_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            classes = Path(tmp) / "classes"
            classes.mkdir()
            sources = sorted(str(p) for p in (ROOT / "bootstrap").rglob("*.java"))
            subprocess.run(["javac", "--release", "8", "-Xlint:-options", "-d", str(classes), *sources], check=True)
            subprocess.run(["java", "-cp", str(classes),
                            "io.github.jackofnonetrades.multi3ify.BootstrapTest", tmp], check=True)


if __name__ == "__main__":
    unittest.main()
