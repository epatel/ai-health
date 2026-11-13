# calibration-corpora

Where the real-world code for calibration and behaviour-preserving checks
lives on this machine. All of it is read-only: never write into these repos
(`scan --save` writes `<path>/.aihealth/`, so pass `--db` with a scratch path).

## Proving existing languages are unaffected

```bash
.venv/bin/python scripts/snapshot.py --lang python,dart,make --out before.json ROOT...
# change the code
.venv/bin/python scripts/snapshot.py --lang python,dart,make --out after.json ROOT...
.venv/bin/python scripts/snapshot.py --compare before.json after.json   # exit 0 = identical
```

Snapshots go in a scratch dir. The full Dart corpus takes about 9 minutes,
so run it in the background.

## Calibration

`scripts/calibrate.py --only <prefix>` scores one group of corpora. Corpora
that are shallow clones are found through an environment variable naming the
directory that holds them.

| Language | Corpora | `--only` / env var |
|---|---|---|
| Python | `.venv/lib/python3.12/site-packages` | |
| Dart | `~/Development/Flutter/flutter/packages`, `~/.pub-cache/hosted/pub.dev` | |
| Makefiles, CMake | `~/Development` (CMake: OrcaSlicer, Pico SDK, Flutter, OpenSCAD; Xcode `SourcePackages/checkouts`) | |
| TypeScript | zod, TanStack Query, tRPC, date-fns, Vite, ky, TypeORM, Excalidraw, Nest | `ts:` / `AIHEALTH_TS_CORPORA` |
| JavaScript | Express, axios, lodash, Preact, webpack, ESLint, three.js | `js:` / `AIHEALTH_JS_CORPORA` |
| Rust | the crates in `~/.cargo/registry/src` (newest version of each, by name) | |
| Shell | bats-core, rbenv, pyenv, n, docker-install, bash-it, Homebrew install, dehydrated, nvm, acme.sh, dokku | `sh:` / `AIHEALTH_SH_CORPORA` |
| zsh | ohmyzsh, prezto, zsh-completions, zsh-z, spaceship-prompt, zsh-autocomplete, powerlevel10k, zinit, zimfw (same directory as shell) | `zsh:` / `AIHEALTH_SH_CORPORA` |
| C, C++ | Pico SDK (`~/Development/pico-sdk`), Flipper (`~/Development/flipper/flipperzero-firmware`, skip its `toolchain/`), OrcaSlicer `src/`, the Flutter engine, Xcode checkouts; clones of lua, cJSON, libuv, redis, curl, fmt, leveldb, googletest, nlohmann/json, abseil | `c` / `AIHEALTH_C_CORPORA` |
| Objective-C | clones of SDWebImage, AFNetworking, fmdb, Mantle, KVOController, Texture, ComponentKit | `objc` / `AIHEALTH_C_CORPORA` |
| Go | the toolchain's `src/` (`go env GOROOT`), `~/go/pkg/mod`; clones of cobra, chi, zap, gin, bubbletea, hugo, prometheus, caddy | `go:` / `AIHEALTH_GO_CORPORA` |
| Swift | Alamofire, swift-argument-parser, swift-collections, swift-nio, Kingfisher, IceCubesApp, vapor, swift-composable-architecture, SwiftLint; firebase-ios-sdk under `~/Library/Developer/Xcode/DerivedData/*/SourcePackages/checkouts` | `swift:` / `AIHEALTH_SWIFT_CORPORA` |
