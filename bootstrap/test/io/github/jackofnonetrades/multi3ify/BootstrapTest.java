package io.github.jackofnonetrades.multi3ify;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;
import java.util.jar.Attributes;
import java.util.jar.JarEntry;
import java.util.jar.JarOutputStream;
import java.util.jar.Manifest;

public final class BootstrapTest {
    private static final String LWJGL3IFY = "me.eigenraven.lwjgl3ify.core.Lwjgl3ifyCoremod";
    private static final String UNIMIXINS = "io.github.legacymoddingmc.unimixins.all.AllCore";
    private static final String LITELOADERLOADER = "com.midnight.liteloaderloader.core.LiteloaderLoader";

    private static Path jar(Path path, String version, String plugin, String modInfo) throws Exception {
        return jar(path, version, "FMLCorePlugin", plugin, modInfo);
    }

    private static Path jar(Path path, String version, String attribute, String value, String modInfo) throws Exception {
        Manifest manifest = new Manifest();
        manifest.getMainAttributes().put(Attributes.Name.MANIFEST_VERSION, "1.0");
        if (value != null) manifest.getMainAttributes().putValue(attribute, value);
        try (JarOutputStream jar = new JarOutputStream(Files.newOutputStream(path), manifest)) {
            jar.putNextEntry(new JarEntry("version.txt"));
            jar.write(version.getBytes("UTF-8"));
            jar.closeEntry();
            if (modInfo != null) {
                jar.putNextEntry(new JarEntry("mcmod.info"));
                jar.write(modInfo.getBytes("UTF-8"));
                jar.closeEntry();
            }
        }
        return path;
    }

    private static Path mod(Path path, String version, boolean lwjgl) throws Exception {
        return jar(path, version, lwjgl ? LWJGL3IFY : null, null);
    }

    private static Path unimixins(Path path, String version) throws Exception {
        return jar(path, version, UNIMIXINS, "[{\"modid\": \"unimixins\", \"name\": \"UniMixins\"}]");
    }

    private static void check(boolean condition, String message) {
        if (!condition) throw new AssertionError(message);
    }

    public static void main(String[] args) throws Exception {
        Path root = java.nio.file.Paths.get(args[0]);
        Path game = root.resolve("instance with spaces");
        Path mods = Files.createDirectories(game.resolve("mods"));
        Path versioned = Files.createDirectories(mods.resolve("1.7.10"));
        Path manual = mod(mods.resolve("renamed-existing-mod.jar"), "old", true);
        Path duplicate = mod(versioned.resolve("lwjgl3ify-old.jar"), "older", true);
        Path unrelated = mod(mods.resolve("unrelated.jar"), "keep", false);
        // Depending on UniMixins must not make a mod look like UniMixins.
        Path dependent = jar(mods.resolve("dependent.jar"), "keep", null,
                "[{\"modid\": \"example\", \"name\": \"Example\", \"requiredMods\": [\"unimixins\"]}]");
        byte[] unrelatedBefore = Files.readAllBytes(unrelated);
        byte[] dependentBefore = Files.readAllBytes(dependent);
        Path first = mod(root.resolve("first.jar"), "first", true);
        String firstHash = Bootstrap.sha256(first);
        Bootstrap.install(game, Bootstrap.LWJGL3IFY, first, firstHash);
        Path installed = mods.resolve(Bootstrap.LWJGL3IFY.managed);
        check(Bootstrap.sha256(installed).equals(firstHash), "fresh install");
        check(!Files.exists(manual) && !Files.exists(duplicate), "manual duplicates backed up");
        try (java.util.stream.Stream<Path> backups = Files.list(game.resolve(".multi3ify-backups"))) {
            check(backups.count() == 2, "both original jars retained");
        }
        Bootstrap.install(game, Bootstrap.LWJGL3IFY, installed, firstHash);
        check(Bootstrap.sha256(installed).equals(firstHash), "repeat install is idempotent");
        Path invalid = mod(root.resolve("invalid.jar"), "bad", true);
        try {
            Bootstrap.install(game, Bootstrap.LWJGL3IFY, invalid, firstHash);
            throw new AssertionError("checksum mismatch must fail");
        } catch (java.io.IOException expected) {}
        check(Bootstrap.sha256(installed).equals(firstHash), "bad download preserves active version");
        Path impostor = mod(root.resolve("impostor.jar"), "not lwjgl3ify", false);
        try {
            Bootstrap.install(game, Bootstrap.LWJGL3IFY, impostor, Bootstrap.sha256(impostor));
            throw new AssertionError("wrong mod must fail even with a matching hash");
        } catch (java.io.IOException expected) {}
        Path second = mod(root.resolve("second.tmp"), "second", true);
        String secondHash = Bootstrap.sha256(second);
        Bootstrap.install(game, Bootstrap.LWJGL3IFY, second, secondHash);
        check(Bootstrap.sha256(installed).equals(secondHash), "upgrade");
        Path rollback = mod(root.resolve("rollback.jar"), "first", true);
        String rollbackHash = Bootstrap.sha256(rollback);
        Bootstrap.install(game, Bootstrap.LWJGL3IFY, rollback, rollbackHash);
        check(Bootstrap.sha256(installed).equals(rollbackHash), "downgrade");

        // Instances that followed the old instructions have the combined jar, or modules, of their own.
        Path manualAll = unimixins(mods.resolve("+unimixins-all-1.7.10-0.3.1.jar"), "0.3.1");
        Path module = jar(versioned.resolve("+unimixins-spongemixins-1.7.10-0.3.1+gtnh.2.0.1.jar"), "module", null,
                "[\n  {\n    \"modid\": \"spongemixins\",\n    \"name\": \"UniMixins: SpongeMixins\"\n  }\n]");
        Path moduleSource = jar(root.resolve("module.jar"), "module", null,
                "[{\"modid\": \"spongemixins\", \"name\": \"UniMixins: SpongeMixins\"}]");
        try {
            Bootstrap.install(game, Bootstrap.UNIMIXINS, moduleSource, Bootstrap.sha256(moduleSource));
            throw new AssertionError("a single module must not replace the combined jar");
        } catch (java.io.IOException expected) {}
        check(Files.exists(manualAll) && Files.exists(module), "rejected download preserves manual UniMixins");
        Path combined = unimixins(root.resolve("combined.jar"), "0.3.2");
        String combinedHash = Bootstrap.sha256(combined);
        Bootstrap.install(game, Bootstrap.UNIMIXINS, combined, combinedHash);
        Path mixins = mods.resolve(Bootstrap.UNIMIXINS.managed);
        check(Bootstrap.sha256(mixins).equals(combinedHash), "UniMixins installed");
        check(!Files.exists(manualAll) && !Files.exists(module), "manual UniMixins jars and modules backed up");
        for (Path original : new Path[] {manualAll, module}) {
            String suffix = "-" + original.getFileName();
            try (java.util.stream.Stream<Path> backups = Files.list(game.resolve(".multi3ify-backups"))) {
                check(backups.anyMatch(p -> p.getFileName().toString().endsWith(suffix)), "retained " + original);
            }
        }
        check(Bootstrap.sha256(installed).equals(rollbackHash), "UniMixins leaves lwjgl3ify alone");
        Bootstrap.install(game, Bootstrap.LWJGL3IFY, installed, rollbackHash);
        check(Bootstrap.sha256(mixins).equals(combinedHash), "lwjgl3ify leaves UniMixins alone");
        check(Arrays.equals(unrelatedBefore, Files.readAllBytes(unrelated))
                && Arrays.equals(dependentBefore, Files.readAllBytes(dependent)), "unrelated mods untouched");

        // The LiteLoader component adds LiteLoader and LiteloaderLoader, replacing manual copies.
        Path manualLite = jar(mods.resolve("liteloader-1.7.10.jar"), "manual", "TweakClass",
                Bootstrap.LITELOADER_TWEAKER, null);
        Path manualLoader = jar(versioned.resolve("LiteloaderLoader-v0.1.2.jar"), "manual", LITELOADERLOADER, null);
        Path lite = jar(root.resolve("liteloader.jar"), "1.7.10_04", "TweakClass", Bootstrap.LITELOADER_TWEAKER, null);
        Path loader = jar(root.resolve("liteloaderloader.jar"), "v0.1.3", LITELOADERLOADER, null);
        String liteHash = Bootstrap.sha256(lite);
        String loaderHash = Bootstrap.sha256(loader);
        try {
            Bootstrap.install(game, Bootstrap.LITELOADER, loader, loaderHash);
            throw new AssertionError("LiteloaderLoader must not pass as LiteLoader");
        } catch (java.io.IOException expected) {}
        Bootstrap.install(game, Bootstrap.LITELOADER, lite, liteHash);
        Bootstrap.install(game, Bootstrap.LITELOADERLOADER, loader, loaderHash);
        Path managedLite = mods.resolve(Bootstrap.LITELOADER.managed);
        Path managedLoader = mods.resolve(Bootstrap.LITELOADERLOADER.managed);
        check(Bootstrap.sha256(managedLite).equals(liteHash) && Bootstrap.sha256(managedLoader).equals(loaderHash),
                "LiteLoader and LiteloaderLoader installed");
        check(!Files.exists(manualLite) && !Files.exists(manualLoader), "manual LiteLoader jars backed up");
        check(Bootstrap.sha256(mixins).equals(combinedHash) && Bootstrap.sha256(installed).equals(rollbackHash),
                "LiteLoader leaves lwjgl3ify and UniMixins alone");

        // No network is needed when the selected mods are already installed.
        System.setProperty("multi3ify.liteloaderSha256", liteHash);
        System.setProperty("multi3ify.liteloaderUrl", "https://invalid.invalid/should-never-be-requested");
        System.setProperty("multi3ify.liteloaderloaderSha256", loaderHash);
        System.setProperty("multi3ify.liteloaderloaderUrl", "https://invalid.invalid/should-never-be-requested");
        System.setProperty("multi3ify.modSha256", rollbackHash);
        System.setProperty("multi3ify.modUrl", "https://invalid.invalid/should-never-be-requested");
        System.setProperty("multi3ify.unimixinsSha256", combinedHash);
        System.setProperty("multi3ify.unimixinsUrl", "https://invalid.invalid/should-never-be-requested");
        System.setProperty("multi3ify.delegate", "com.gtnewhorizons.retrofuturabootstrap.TestDelegate");
        String[] forwarded = {"--gameDir", game.toString(), "--username", "test"};
        try { Bootstrap.main(forwarded); } catch (Throwable e) { throw new AssertionError(e); }
        check(Arrays.equals(forwarded, com.gtnewhorizons.retrofuturabootstrap.TestDelegate.arguments), "game arguments forwarded");
        check(Thread.currentThread() == com.gtnewhorizons.retrofuturabootstrap.TestDelegate.thread, "macOS main thread preserved");
        check(Files.exists(managedLite) && Files.exists(managedLoader), "selected LiteLoader kept");

        // Removing the LiteLoader component removes only the jars the helper manages.
        Path otherLite = jar(mods.resolve("unrelated-tweaker.jar"), "keep", "TweakClass", "example.Tweaker", null);
        System.clearProperty("multi3ify.liteloaderSha256");
        System.clearProperty("multi3ify.liteloaderUrl");
        System.clearProperty("multi3ify.liteloaderloaderSha256");
        System.clearProperty("multi3ify.liteloaderloaderUrl");
        try { Bootstrap.main(forwarded); } catch (Throwable e) { throw new AssertionError(e); }
        check(!Files.exists(managedLite) && !Files.exists(managedLoader), "deselected LiteLoader removed");
        check(Files.exists(otherLite) && Bootstrap.sha256(mixins).equals(combinedHash)
                && Bootstrap.sha256(installed).equals(rollbackHash), "required mods and other tweakers kept");
        for (String hash : new String[] {liteHash, loaderHash}) {
            try (java.util.stream.Stream<Path> backups = Files.list(game.resolve(".multi3ify-backups"))) {
                check(backups.anyMatch(p -> p.getFileName().toString().startsWith(hash)), "removed jar backed up");
            }
        }
        System.out.println("Bootstrap install, upgrade, rollback, migration, UniMixins, LiteLoader, verification,"
                + " and offline launch passed");
    }
}
