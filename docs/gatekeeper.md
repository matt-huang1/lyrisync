# Gatekeeper, and why a downloaded build is blocked

If you built LyriSync yourself, none of this applies: an app you compiled
on your own Mac is never marked as downloaded, so it opens with an
ordinary double-click. This page is about the release zip.

## What actually happens

Download the zip in a browser and macOS attaches an extended attribute,
`com.apple.quarantine`, to it — and to the app inside once you unzip it.
The flag is applied by the *downloading application*, not by the network:
Safari, Chrome and Mail set it; `curl` does not.

When you open a quarantined app for the first time, macOS asks Gatekeeper
whether it approves. For LyriSync the answer is no:

```
$ codesign -dv /Applications/LyriSync.app
Signature=adhoc
TeamIdentifier=not set

$ spctl -a -vvv -t exec /Applications/LyriSync.app
/Applications/LyriSync.app: rejected
```

**The app is signed ad-hoc rather than with an Apple Developer ID, and it
is not notarised.** An ad-hoc signature is enough to make the code run on
Apple silicon at all, and it means the bundle has not been altered since
it was signed — but it carries no identity. There is no developer account
behind it, so there is nobody for macOS to name and nobody for Apple to
revoke. Gatekeeper's refusal is correct, not a bug or an oversight.

Note that `spctl` says *rejected* for a locally built copy too. The
difference is not the signature — it is that a locally built app has no
quarantine flag, so macOS never runs the assessment when you launch it.

## The two ways past it

Both are decisions, so make them knowingly. If you are not comfortable
making them, build from source instead — it is four commands and it is
the option that requires trusting nobody.

**Through System Settings.** Try to open the app; macOS refuses. Then go
to System Settings → Privacy & Security, scroll to the Security section,
find the message naming LyriSync and click **Open Anyway**. Confirm at the
prompt. This is Apple's supported route: it records your decision for that
one app and leaves everything else unchanged.

**By removing the flag yourself.**

```sh
xattr -d com.apple.quarantine /Applications/LyriSync.app
```

This strips the quarantine attribute, after which the app launches like
any locally built one. It does exactly what the System Settings route
does, without the dialog. It is also a blunter instrument: run it only on
a path you chose deliberately, and never as a habit on a whole
directory — the flag is the only thing standing between a downloaded
binary and an ordinary double-click.

Whichever you use, check the download first:

```sh
shasum -a 256 ~/Downloads/LyriSync-1.0.0.zip
# 52f7ac2bb5665d9b787d27c6a1c92d8cd22d0eadf21da677d52a1a15cba9482e
```

## Notarisation is the real fix, and it is not in place

The honest position: this app should be notarised, and it is not.

Notarisation means uploading the signed build to Apple, who scan it for
known malware and issue a ticket that gets stapled to the bundle.
Gatekeeper then approves it on first launch and the user sees nothing
unusual. It requires a paid Apple Developer account and a Developer ID
certificate, neither of which exists for this project.

**What notarisation would tell you:** that a specific, named developer
account signed this exact build; that Apple's automated scan found no
known malware in it; and that if the developer's certificate is later
revoked, macOS can refuse to run it.

**What it would not tell you:** that the app is well written, that it does
what its README claims, that it does not do anything you would object to,
or that anybody at Apple read the source. Notarisation is an automated
malware scan plus an accountable identity. It is not a review, and it is
not a safety guarantee.

So the gap between this app and a notarised one is narrower than the
warning implies — and wider than nothing. Judge it on that basis rather
than on the presence or absence of a dialog.

## Verifying this build

Three things you can check without trusting a claim on this page:

- **The hash.** The command above is the whole of it. If it does not match
  the value published here and in the README, do not open the file.
- **The provenance.** The release is built from the `v1.0.0` tag, and that
  tag is in this repository. The zip contains one thing, `LyriSync.app`,
  which is the PyInstaller output described in
  [packaging](packaging.md) — icon, freeze, ad-hoc signature, nothing
  else. `main` moves ahead of the tag between releases, so the released
  build is the tag, not the tip.
- **The source.** All of it is public, including the spec file that
  produces the bundle and the tests that run on every push.

And the zero-trust option remains: build it yourself. The instructions are
on the [README](../README.md), it takes four commands, and it produces a
copy with no quarantine flag, no dialog and no decision to make about
anybody else's word.
