# multi3ify

Prism metadata for Minecraft 1.7.10 with [lwjgl3ify](https://github.com/GTNewHorizons/lwjgl3ify) and Minecraft 1.12.2 with [Cleanroom](https://github.com/CleanroomMC/Cleanroom/).

Also supports Fjord Launcher: authlib-injector and Loki metadata are mirrored from
[Fjord's metadata repository](https://github.com/unmojang/meta-launcher), alongside
the standard Prism packages and custom Minecraft profiles.

## Setup

1. Set **Settings -> APIs -> Services -> Metadata Server** to:

   ```text
   https://angelicindustries.github.io/multi3ify/
   ```

   Or more memorable:

   ```text
   https://meta.angelic.industries
   ```

2. Create one of these profiles, leaving **Mod Loader -> None**:

   | Profile | Installed automatically | Add to `mods` yourself |
   | --- | --- | --- |
   | **`1.7.10-lwjgl3ify`** | Matching Forge, lwjgl3ify runtime and mod | [UniMixins combined jar](https://github.com/LegacyModdingMC/UniMixins/releases) (0.1.23+) |
   | **`1.12.2-cleanroom`** | Cleanroom loader and its runtime libraries | [Fugue](https://www.curseforge.com/minecraft/mc-mods/fugue) and [Scalar Legacy](https://www.curseforge.com/minecraft/mc-mods/scalar-legacy) for conventional Forge packs |

3. Enable automatic Java selection/download, clear any forced Java 8 override, and launch.

## Existing instances

Select **Edit Instance -> Version -> Minecraft -> Change version**, then choose
**`1.7.10-lwjgl3ify`** for a 1.7.10 instance or **`1.12.2-cleanroom`** for a 1.12.2 instance.
Changing Minecraft removes the old LWJGL 2 dependency; changing only Forge leaves it installed.
Revert any old local Minecraft/Forge, LWJGL, lwjgl3ify or Cleanroom component patches first.
Worlds, mods, and configuration stay in place.
For Cleanroom, review the upstream [modpack migration guide](https://cleanroommc.com/wiki/end-user-guide/preparing-your-modpack)
for pack-specific compatibility changes.

## Choose a release

After creating the instance: **Edit Instance -> Version -> lwjgl3ify / Cleanroom -> Change version**.

- **`latest`** follows stable lwjgl3ify releases or published Cleanroom releases
  (including alpha releases), checked every six hours.
- **Numbered versions** stay pinned. Switching also supports downgrades.

The next launch uses your selection. Restart the launcher if its version list is stale.
When downgrading Cleanroom, you may also need an older Fugue version.

## Self-host

Fork, enable Actions, set **Settings -> Pages -> Source -> GitHub Actions**, then run
**Update and publish metadata**. Use your Pages URL ending in `/v1/`.

Optional repository Actions variables:

| Variable | Default |
| --- | --- |
| `PRISM_META_REPOSITORY` | `PrismLauncher/meta-launcher` |
| `PRISM_META_REF` | Upstream default branch |
| `LWJGL3IFY_MIN_VERSION` | `3.0.0` |
| `CLEANROOM_MIN_VERSION` | `0.6.13-alpha` |
