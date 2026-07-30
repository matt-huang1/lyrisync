# Making a release

Ten minutes, all of it on one Mac. Bundle building is deliberately
[not in CI](testing-and-ci.md#bundle-building-stays-out-of-ci): the
artefact is macOS-only and the things worth checking about it can only be
accepted by a person.

## The sequence

### 1. Bump the version

In `pyproject.toml`, under `[project]`:

```toml
version = "1.1.0"
```

That is the only place a version is written down. Everything else — the
bundle's `CFBundleShortVersionString` and `CFBundleVersion`, the
User-Agent sent to LRCLIB — is derived from it. See
[packaging](packaging.md#the-version-is-never-written-in-the-spec).

### 2. Reinstall, or the metadata is stale

```sh
.venv/bin/pip install -e ".[build]"
```

**Not optional.** The version is read from the *installed* distribution's
metadata, and an editable install's metadata is a snapshot taken at
install time. Edit `pyproject.toml` without reinstalling and the bundle
claims the old version.

The build refuses rather than letting that through — it compares the two
and stops with a message naming both — and the test suite carries the same
comparison, so `make test` catches it before the build does.

### 3. Build

```sh
make app
```

Icon, freeze, ad-hoc signature. Produces `dist/SottoVoce.app`.

### 4. Package with `ditto`, never `zip`

```sh
cd dist && ditto -c -k --keepParent SottoVoce.app SottoVoce-1.1.0.zip
```

`ditto` is the macOS archiver and preserves symlinks, resource forks and
the signature. `zip -r` follows symlinks and stores a full copy of each
target instead — and a PyInstaller bundle is full of them, because
Python.framework and every Qt framework carry `Versions/Current` trees.

Measured on the 1.0.0 bundle: **36.1 MB with `ditto`, 187.0 MB with
`zip -r`**, from 109 symlinks. The naive archive is five times the size
and its signature does not survive the round trip.

### 5. Tag

```sh
git tag -a v1.1.0 -m "SottoVoce 1.1.0"
git push origin v1.1.0
```

The tag is what the release is built from, and `main` moves ahead of it
between releases. Tag the commit you built, not the tip.

### 6. Publish and upload

Create the GitHub release against that tag and attach the zip. One asset,
named for the version.

### 7. Record the hash

```sh
shasum -a 256 dist/SottoVoce-1.1.0.zip
```

Put that value, and the new download link, in the **README's install
section**. That is the one place the hash lives — `docs/gatekeeper.md`
tells readers to check against the README rather than repeating it,
because a hash duplicated across two files is one that will eventually
disagree with itself, and the person who notices will be someone deciding
whether to trust a binary.

Verify what you published rather than what you built:

```sh
curl -sL -o /tmp/check.zip https://github.com/matt-huang1/sottovoce/releases/download/v1.1.0/SottoVoce-1.1.0.zip
shasum -a 256 /tmp/check.zip
```

**The hash changes on any re-upload.** Rebuilding produces a different
archive even from identical source — timestamps and file ordering differ —
so replacing the asset, even with "the same" build, invalidates the
published value. If you re-upload, update the README in the same breath.

## Afterwards

Update [the changelog](../CHANGELOG.md), and check that the test count in
[testing and CI](testing-and-ci.md) still matches `make test`. That page is
the only place the number is written down — it used to be in the README
too, which meant two numbers to keep true and one that quietly was not.
