# Cleanroom fixtures

`cleanroom-0.7.4-alpha.zip` is the unmodified instance pack from the
[0.7.4-alpha release](https://github.com/CleanroomMC/Cleanroom/releases/tag/0.7.4-alpha).
Its SHA-256 is `8ea9db8d9548f84ef2848cb3b90c2684da123c1171999d5ca9b59d89734ac457`.
Tests use its real component and native-library layout, replacing the universal
jar download with synthetic bytes so unit tests need no network access.

`cleanroom-0.7.5-alpha.zip` is the unmodified pack from the
[0.7.5-alpha release](https://github.com/CleanroomMC/Cleanroom/releases/tag/0.7.5-alpha),
with SHA-256 `dd4c5ba2e8ecf1bdfe3523d72c646e4e61a3b093d56013b83fbdb44a8264cda4`.
It covers newly added empty overrides for vanilla JNA `platform` and the omitted
LWJGL 2 package's `jutils` dependency.

`cleanroom-mods.json` pins the companion mods used only by the Linux launch test.
Fugue 0.25.1 targets Cleanroom 0.7.3-alpha and later; these fixtures do not install
or manage mods in users' instances.

Both isolated launch instances disable the splash screen. Cleanroom 0.6.13 has a
missing-companion prompt race; 0.7.4 can intermittently fail in the splash SDL
event pump with `ConcurrentModificationException` during Foundation transformer
registration. The test still requires full game/mod initialization and unchanged
mod jars. Published profiles retain upstream behavior.
