package io.github.jackofnonetrades.multi3ify;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.lang.reflect.InvocationTargetException;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.channels.FileChannel;
import java.nio.channels.FileLock;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.DirectoryStream;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardCopyOption;
import java.nio.file.StandardOpenOption;
import java.security.MessageDigest;
import java.util.ArrayList;
import java.util.List;
import java.util.jar.JarEntry;
import java.util.jar.JarFile;
import java.util.jar.Manifest;
import java.util.regex.Pattern;

/** Installs the metadata-selected mods before RFB/Forge discovers mods. */
public final class Bootstrap {
    static final Mod LWJGL3IFY = new Mod("lwjgl3ify", "lwjgl3ify-multi3ify-managed.jar", "multi3ify.mod",
            "me.eigenraven.lwjgl3ify.core.Lwjgl3ifyCoremod", null);
    // Upstream's "+" prefix sorts it before mods that embed an older Mixin. Every
    // UniMixins jar, including the separate modules, names itself in mcmod.info.
    static final Mod UNIMIXINS = new Mod("UniMixins", "+unimixins-multi3ify-managed.jar", "multi3ify.unimixins",
            "io.github.legacymoddingmc.unimixins.all.AllCore",
            Pattern.compile("\"name\"\\s*:\\s*\"UniMixins(\"|: )"));
    static final Mod[] MODS = {LWJGL3IFY, UNIMIXINS};

    static final class Mod {
        final String name;
        final String managed;
        final String property;
        final String plugin;
        final Pattern modInfo;

        Mod(String name, String managed, String property, String plugin, Pattern modInfo) {
            this.name = name;
            this.managed = managed;
            this.property = property;
            this.plugin = plugin;
            this.modInfo = modInfo;
        }

        /** True for the exact jar the metadata selects. */
        boolean isRelease(Path path) {
            return matches(path, plugin, null);
        }

        /** True for any copy that would load alongside the managed jar. */
        boolean isCopy(Path path) {
            return Files.isRegularFile(path) && path.getFileName().toString().endsWith(".jar")
                    && matches(path, plugin, modInfo);
        }
    }

    private Bootstrap() {}

    public static void main(String[] args) throws Throwable {
        checkRuntime();
        String[] digests = new String[MODS.length];
        for (int i = 0; i < MODS.length; i++) {
            digests[i] = required(MODS[i].property + "Sha256");
            if (!digests[i].matches("[a-f0-9]{64}")) {
                throw new IOException("Invalid " + MODS[i].name + " SHA-256 in launcher metadata");
            }
        }
        Path game = Paths.get(".").toAbsolutePath().normalize();
        for (int i = 0; i < args.length - 1; i++) {
            if (args[i].equals("--gameDir")) {
                game = Paths.get(args[i + 1]).toAbsolutePath().normalize();
            }
        }
        Files.createDirectories(game);
        try (FileChannel channel = FileChannel.open(game.resolve(".multi3ify.lock"),
                StandardOpenOption.CREATE, StandardOpenOption.WRITE);
             FileLock lock = channel.tryLock()) {
            if (lock == null) {
                throw new IOException("Another multi3ify launch is updating this instance");
            }
            Path mods = game.resolve("mods");
            Files.createDirectories(mods);
            Path[] sources = new Path[MODS.length];
            List<Path> downloads = new ArrayList<>();
            try {
                // Download everything first, so a network failure leaves every mod as it was.
                for (int i = 0; i < MODS.length; i++) {
                    sources[i] = mods.resolve(MODS[i].managed);
                    if (!Files.isRegularFile(sources[i]) || !sha256(sources[i]).equals(digests[i])) {
                        Path download = Files.createTempFile(mods, ".multi3ify-", ".tmp");
                        downloads.add(download);
                        System.out.println("[multi3ify] Installing the selected " + MODS[i].name + " version...");
                        download(MODS[i], required(MODS[i].property + "Url"), download);
                        sources[i] = download;
                    }
                }
                for (int i = 0; i < MODS.length; i++) install(game, MODS[i], sources[i], digests[i]);
            } finally {
                for (Path download : downloads) Files.deleteIfExists(download);
            }
        }
        // Calling on this thread preserves upstream's macOS first-thread entry point.
        String delegate = required("multi3ify.delegate");
        if (!delegate.startsWith("com.gtnewhorizons.retrofuturabootstrap.")) {
            throw new IOException("Unexpected lwjgl3ify launch entry point: " + delegate);
        }
        try {
            Class.forName(delegate).getMethod("main", String[].class).invoke(null, (Object) args);
        } catch (InvocationTargetException e) {
            throw e.getCause();
        }
    }

    private static void checkRuntime() throws IOException {
        // Look for an LWJGL 2-only resource without loading/initializing any LWJGL
        // classes. Loading PointerBuffer here can bind the wrong implementation,
        // and native initialization must remain on upstream's macOS main thread.
        URL legacy = Bootstrap.class.getClassLoader().getResource("org/lwjgl/LWJGLException.class");
        if (legacy != null) {
            throw new IOException("LWJGL 2 is still on this instance's classpath, alongside lwjgl3ify's LWJGL 3.\n"
                    + "In your launcher, select Edit Instance -> Version -> Minecraft -> Change version"
                    + " -> 1.7.10-lwjgl3ify.\n"
                    + "Changing only the Forge version leaves the ordinary Minecraft runtime installed.\n"
                    + "If the special Minecraft profile is already selected, remove the leftover LWJGL 2"
                    + " component or local patch from the Version tab.\n"
                    + "No mods were changed by this launch. Conflicting LWJGL 2 resource: " + legacy);
        }
    }

    private static String required(String name) throws IOException {
        String value = System.getProperty(name);
        if (value == null || value.isEmpty()) throw new IOException("Missing metadata property: " + name);
        return value;
    }

    private static void download(Mod mod, String address, Path destination) throws IOException {
        URL url = new URL(address);
        for (int redirect = 0; redirect < 6; redirect++) {
            if (!url.getProtocol().equals("https")) throw new IOException("Mod downloads require HTTPS");
            HttpURLConnection connection = (HttpURLConnection) url.openConnection();
            connection.setConnectTimeout(20000);
            connection.setReadTimeout(90000);
            connection.setInstanceFollowRedirects(false);
            connection.setRequestProperty("User-Agent", "multi3ify-bootstrap");
            try {
                int status = connection.getResponseCode();
                if (status >= 300 && status < 400) {
                    String location = connection.getHeaderField("Location");
                    if (location == null) throw new IOException("Missing download redirect location");
                    url = new URL(url, location);
                    continue;
                }
                if (status != 200) throw new IOException(mod.name + " download returned HTTP " + status);
                try (InputStream input = connection.getInputStream()) {
                    Files.copy(input, destination, StandardCopyOption.REPLACE_EXISTING);
                }
                return;
            } finally {
                connection.disconnect();
            }
        }
        throw new IOException("Too many " + mod.name + " download redirects");
    }

    static String sha256(Path path) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        try (InputStream input = Files.newInputStream(path)) {
            byte[] buffer = new byte[65536];
            int count;
            while ((count = input.read(buffer)) != -1) digest.update(buffer, 0, count);
        }
        StringBuilder hex = new StringBuilder();
        for (byte b : digest.digest()) hex.append(String.format("%02x", b & 255));
        return hex.toString();
    }

    private static boolean matches(Path path, String plugin, Pattern modInfo) {
        try (JarFile jar = new JarFile(path.toFile())) {
            Manifest manifest = jar.getManifest();
            if (manifest != null && plugin.equals(manifest.getMainAttributes().getValue("FMLCorePlugin"))) {
                return true;
            }
            JarEntry info = modInfo == null ? null : jar.getJarEntry("mcmod.info");
            if (info == null) return false;
            ByteArrayOutputStream text = new ByteArrayOutputStream();
            try (InputStream input = jar.getInputStream(info)) {
                byte[] buffer = new byte[8192];
                int count;
                while ((count = input.read(buffer)) != -1) text.write(buffer, 0, count);
            }
            return modInfo.matcher(new String(text.toByteArray(), StandardCharsets.UTF_8)).find();
        } catch (IOException e) {
            return false;
        }
    }

    static void install(Path game, Mod mod, Path source, String expectedDigest) throws Exception {
        // Verify before touching either the managed mod or any existing manual copies.
        if (!sha256(source).equals(expectedDigest) || !mod.isRelease(source)) {
            throw new IOException(mod.name + " download failed verification; existing mods were preserved");
        }
        Path mods = game.resolve("mods");
        Files.createDirectories(mods);
        Path managed = mods.resolve(mod.managed);
        List<Path> duplicates = new ArrayList<>();
        for (Path directory : new Path[] {mods, mods.resolve("1.7.10")}) {
            if (!Files.isDirectory(directory)) continue;
            try (DirectoryStream<Path> files = Files.newDirectoryStream(directory, "*.jar")) {
                for (Path file : files) {
                    if (!file.equals(managed) && mod.isCopy(file)) duplicates.add(file);
                }
            }
        }
        if (!source.equals(managed)) {
            // Keep a rollback copy of the previous managed version too.
            if (Files.exists(managed)) backup(game, managed);
            try {
                Files.move(source, managed, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            } catch (AtomicMoveNotSupportedException e) {
                Files.move(source, managed, StandardCopyOption.REPLACE_EXISTING);
            }
        }
        for (Path duplicate : duplicates) {
            backup(game, duplicate);
            Files.delete(duplicate);
        }
    }

    private static void backup(Path game, Path file) throws Exception {
        Path backups = game.resolve(".multi3ify-backups");
        Files.createDirectories(backups);
        Path target = backups.resolve(sha256(file) + "-" + file.getFileName());
        if (!Files.exists(target)) Files.copy(file, target);
        System.out.println("[multi3ify] Saved previous mod to " + target);
    }
}
