Integration design, researched and implemented 2026-09-20. The implementation is in `scripts/cleanroom.py`; launch validation uses `scripts/smoke_cleanroom.py`.

Add `1.12.2-cleanroom` alongside `1.7.10-lwjgl3ify`. Automatically manage the Cleanroom loader and its complete launcher runtime. Leave Fugue, Scalar, and all other mods under the user's control. Fugue compatibility when changing Cleanroom versions is the user's responsibility; include only a short note in the eventual README instructions.

Cleanroom is a Forge replacement for Minecraft 1.12.2, with modern Java, LWJGL 3, a replacement launch system, and integrated Mixin support. It is a separate target from the existing 1.7.10 integration. Upstream officially supports MultiMC-derived launchers, which fits multi3ify's approach. [Cleanroom documentation](https://github.com/CleanroomMC/Cleanroom/tree/0.6.13-alpha#readme)

What belongs in the installation:

| Component | Purpose and proposed handling |
| --- | --- |
| Cleanroom universal jar and the libraries in its released instance profile | Install automatically through Prism metadata. Keep the complete runtime tied to the selected Cleanroom release. |
| Java | Declare the selected release's compatible majors and the appropriate Prism runtime name, allowing Prism to select/download it. |
| Fugue | User-installed compatibility patches for existing Forge mods. Recommend it in setup instructions; no installation, update, downgrade, or compatibility management by multi3ify. |
| Scalar Legacy | User-installed Scala 2.11 provider for legacy Forge mods, including original OpenComputers and ProjectRed. Use this name and link explicitly in the normal migration instructions. |
| Scalar (Scala 3) | A different provider for mods developed against Scala 3, such as OpenComputers Rescaled. Choose according to the pack; do not treat it as an update to Scalar Legacy or install both. |
| Mixin / MixinBooter / ConfigAnytime | Already integrated into Cleanroom. No separate UniMixins-like installation step. |
| Relauncher | Not needed for this direct Prism metadata installation. |
| Forgelin-Continuous, LibrarianLib-Continuous, other replacements and optimization mods | Pack-specific decisions, left to the user. |

Fugue patches compatibility problems; Scala support was intentionally separated from the loader. Scalar Legacy and Scalar are separate projects because they support different generations of mods. The Scalar repository explicitly warns against installing both. [Fugue](https://www.curseforge.com/minecraft/mc-mods/fugue), [Scalar Legacy](https://www.curseforge.com/minecraft/mc-mods/scalar-legacy), [Scalar](https://www.curseforge.com/minecraft/mc-mods/scalar), [Scalar README](https://github.com/CleanroomMC/Scalar/blob/scala3/README.md)

These companions are not an exact-version pair like lwjgl3ify's mod and Forge patches. They are not arbitrarily interchangeable either: the inspected Fugue 0.24.4 release jar declares `cleanroom@[0.6.10-alpha,)` and targets Java 25. Keep this research fact out of runtime enforcement; the user manages the combination. Proposed README note: “When downgrading Cleanroom, you may also need an older Fugue version.” [Fugue dependency declaration](https://github.com/CleanroomMC/Fugue/blob/0.24.4/src/main/java-templates/com/cleanroommc/fugue/Reference.java), [its Cleanroom build version](https://github.com/CleanroomMC/Fugue/blob/0.24.4/gradle.properties)

Cleanroom itself checks for Fugue and Scalar. Missing companions can cause a confirmation prompt, including in the tested empty instance because discovery also examines classpath jars. They are not unconditional dependencies: acknowledging the prompt lets the empty instance start. Preserve upstream behavior; do not change `forge_early.cfg` or add a second check. [Presence check](https://github.com/CleanroomMC/Cleanroom/blob/0.6.13-alpha/src/main/java/com/cleanroommc/common/PatchModPresentChecker.java), [client startup](https://github.com/CleanroomMC/Cleanroom/blob/0.6.13-alpha/src/main/java/net/minecraftforge/fml/client/FMLClientHandler.java#L226)

The inspected baseline is [Cleanroom 0.6.13-alpha](https://github.com/CleanroomMC/Cleanroom/releases/tag/0.6.13-alpha), published September 12, 2026. Its GitHub release is marked `prerelease: false` despite the `-alpha` version suffix. The downloaded `cleanroom-0.6.13-alpha.zip` was verified against the release asset's size and SHA-256. It contains three component patches and no mods:

| Released component | Relevant contents |
| --- | --- |
| `net.minecraft` | Minecraft 1.12.2 jar/assets/arguments, Java majors `[25, 26]`, and a small library list. |
| `net.minecraftforge` | Named Cleanroom; universal jar, Foundation, lwjglxx, and the remaining libraries; main class `top.outlands.foundation.boot.Foundation`; FML tweaker; UTF-8 JVM property. |
| `org.lwjgl3` | LWJGL 3.4.1 libraries and native jars, with platform rules. |

Use the **released ZIP**, not the repository's template files: the template still names Bouncepad, while the generated release launches Foundation. The generator replaces template fields when packaging. The release's Minecraft patch also retains an old `org.lwjgl3` suggestion of `3.3.1`, whereas the actual component and pack select `3.4.1`; take the runtime from the included component. [Pack generation code](https://github.com/CleanroomMC/Cleanroom/blob/0.6.13-alpha/buildSrc/src/main/groovy/com/cleanroommc/gradle/helpers/tasks/CreateMMCPackTask.groovy)

Implementation sequence:

1. **Add a dedicated Cleanroom release reader.**

   Add `scripts/cleanroom.py`, reusing the existing fetch, asset verification, JSON, and metadata publication helpers from `scripts/build_metadata.py`. Keep the lwjgl3ify release filter unchanged. Cleanroom needs its own version parser and release policy because the current stable-semver-only filter rejects its published alpha tags.

   Start support at `0.6.13-alpha`; do not claim historical layouts work without inspecting them. Discover subsequent published, non-draft, non-prerelease release assets, explicitly accepting the supported alpha tag format. Provide `CLEANROOM_MIN_VERSION` / `--cleanroom-minimum` independently of the existing lwjgl3ify setting. Require the exact ZIP and universal jar assets, expected component identities, Minecraft 1.12.2, known fields, valid downloads, and a supported entry point. Reject unexpected changes before publication. Action builds and installer execution are outside the first integration.

   Use a separate cache namespace/schema for Cleanroom. Verify the ZIP's asset digest, the universal jar's asset digest, and its agreement with the size/SHA-1 in the runtime profile. Preserve direct upstream artifact downloads. Record fingerprints so a replaced upstream asset cannot silently rewrite an already published numbered profile.

2. **Publish the same selection pattern as lwjgl3ify.**

   Proposed dependency chain:

   ```text
   net.minecraft / 1.12.2-cleanroom
       -> net.minecraftforge / 1.12.2-cleanroom-latest  (dependency-only bridge)
           -> io.github.jackofnonetrades.cleanroom / latest or numbered release
   ```

   The Minecraft entry reports payload version `1.12.2`, preserving mod searches. The Forge bridge is named `Forge (Cleanroom)` and carries dependencies only, making it distinguishable from the `Cleanroom` release picker. Reusing the Forge UID follows upstream's instance layout, occupies the existing Forge component slot, and retains Forge identification for mod browsing. It must not load an ordinary Forge jar. The dedicated Cleanroom component owns version selection, with `latest` and immutable numbered releases. Describe `latest` as following published Cleanroom releases, including alpha releases; do not call them stable.

   Build the Minecraft alias from the released Minecraft patch's game fields, retaining jar, assets and arguments. Remove its runtime libraries, Java declarations, main class and original component requirements, then attach the bridge. Put all release-specific libraries, Java requirements, launch arguments, tweakers, traits and entry point in the dedicated runtime component. This prevents a later-applied base or bridge from restoring vanilla runtime settings and makes changing the Cleanroom row change the whole runtime together. Derive component composition order from the released pack; its patches do not carry the `order` fields required by the lwjgl3ify parser.

   Preserve rules, native classifiers, artifact paths and hashes. The narrator appears as both an ordinary jar and a native-bearing entry under the same Maven name; these entries must not be collapsed by name alone. Do not add a dependency on Prism's independently selectable LWJGL package, which could separate LWJGL from the selected Cleanroom runtime.

   Generalize `reported_version()` with an explicit alias mapping for the two supported Minecraft entries. Generalize recommended-version handling in `add_versions()` and generated-component recognition in the verifier. Preserve all ordinary upstream documents and the existing lwjgl3ify profiles, bridge, archived Forge metadata, and helper jars.

3. **Complete Java selection and keep the existing bootstrap scoped to lwjgl3ify.**

   The Cleanroom ZIP declares compatible Java majors but omits `compatibleJavaName`. Prism's automatic download path requires that name. The current Prism feed publishes Java 25 as `java-runtime-epsilon`; validate the checked-out feed and assign that name for this Cleanroom baseline. Preserve `[25, 26]` as declared compatibility while preferring the available Java 25 runtime. Future releases need an explicit supported mapping; do not guess a runtime name or inherit Java 8 from ordinary 1.12.2. [Prism automatic installation](https://github.com/PrismLauncher/PrismLauncher/blob/43a67faef1fc22c26cd9abe6336d631a292961f8/launcher/minecraft/launch/AutoInstallJava.cpp#L102), [Java 25 metadata](https://meta.prismlauncher.org/v1/net.minecraft.java/java25.json)

   There is no separate Cleanroom mod to synchronize into `mods/`. Launch Foundation directly. Do not reuse or generalize `Bootstrap.java` for this integration: it installs lwjgl3ify and restricts its delegate to RetroFuturaBootstrap. Ignore the ZIP's `instance.cfg`, which contains a placeholder Java path and disables Java compatibility checks; metadata should supply a valid runtime instead. macOS startup behavior must follow Cleanroom's launch system and be tested independently of lwjgl3ify's first-thread handling.

4. **Extend verification and CI before publishing.**

   Add focused fixtures and tests for Cleanroom release filtering, malformed/changed archives, library/native preservation, the second Minecraft alias, dependency resolution, Java selection metadata, and numbered version stability as `latest` moves. Update `scripts/verify_metadata.py` to check both integrations explicitly; its current assumptions identify generated entries by `lwjgl3ify` and expect exactly one added Forge entry.

   Verify that Cleanroom's resolved runtime has no original Forge jar, original LaunchWrapper jar, LWJGL 2 component, Java 8 declaration, or multi3ify mod-install properties. Validate the intended runtime chain and inspect both new-instance and existing-instance resolution in Prism. In particular, confirm the bridge replaces an existing Forge selection and switching versions keeps a pinned Cleanroom component. Do not assume a suggested dependency overrides every preexisting explicit selection; adjust bridge dependency constraints if the launcher check shows otherwise.

   Add a separate Cleanroom smoke path or `scripts/smoke_cleanroom.py`. Reuse downloads and isolated-instance setup where useful, but preserve Prism's distinction between ordinary and extracted-native libraries. The existing smoke test is specifically written for 1.7.10, UniMixins, RetroFuturaBootstrap, legacy assets, and its current duplicate-library cases.

   Test an empty Cleanroom instance, then an isolated instance with explicit test copies of Fugue and Scalar Legacy plus a small representative Forge mod. Those test dependencies belong only to CI. Pin their URLs/checksums for reproducibility; they must not become production-managed dependencies. Confirm complete startup. Disable the splash screen in both isolated test instances: Cleanroom 0.6.13-alpha can race while rendering its missing-companion prompt, and 0.7.4-alpha can race Foundation transformer registration while the splash thread pumps SDL events. Both tests still require full initialization; published metadata retains upstream behavior. Verify installed mod jars remain unchanged while allowing exactly Scalar's declared bundled dependencies to be extracted. Test release switching in the generated metadata and launch both `latest` and its numbered release.

   Run the Cleanroom smoke test with Java 25, retaining an independently selected compatible Java for the existing lwjgl3ify smoke test. Extend `.github/workflows/pages.yml`, cache configuration, `status.json` and `sync-state.json` with Cleanroom data while preserving the current lwjgl3ify fields. Keep publication after both integration checks pass. Confirm fresh creation, Java download, mod browser filtering, migration, pinning, and rollback in actual Prism; verify Windows and macOS launches before claiming those platforms tested.

5. **Document the user flow.**

   Add a second setup path to `README.md` and `site/index.html`: choose `1.12.2-cleanroom`, leave Mod Loader at None, install Fugue and Scalar Legacy for a conventional Forge pack, enable automatic Java selection/download, and launch. Point Scala 3 packs to the appropriate provider. Select releases through the Cleanroom row in the Version tab. Include the single Fugue downgrade sentence above in the README; add no compatibility-management feature or separate warning flow.

   Existing instances must change the Minecraft row, because changing only the loader leaves vanilla LWJGL 2 dependencies. Revert/remove old local loader, Minecraft and LWJGL patches as necessary. Link to upstream's migration guide for pack-specific changes rather than automatically replacing mods. The guide includes actual incompatibilities and optional recommendations; do not treat every suggested replacement as mandatory. It also calls out running Bansoukou 4 packs once before conversion. [Preparing your modpack](https://cleanroommc.com/wiki/end-user-guide/preparing-your-modpack)

Validation: the combined metadata feed builds and its checksum/dependency chain verifies. Unit tests cover release filtering, archive verification, native handling, Java selection, both integrations, and immutable numbered releases. Isolated Linux launches exercise lwjgl3ify with UniMixins and Cleanroom 0.7.4 with both an empty instance and Fugue 0.25.1, Scalar Legacy 1.0.1 and JEI 4.16.1.1013 on Java 25. The importer also verifies all 0.7.0–0.7.4 release packs and preserves the published 0.6.13 runtime hash. Interactive Prism migration and Windows/macOS launch validation remain manual checks; they are not claimed as tested by the Linux smoke script.
