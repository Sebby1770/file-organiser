# Signing and trusted release checklist

The current workflow creates **unsigned technical-evaluator builds**. A green build does not mean macOS notarisation, Windows reputation, multi-architecture support, or end-user trust is established.

Do not put certificates, private keys, passwords, API keys, or notarisation credentials in the repository. Store them in protected GitHub environments or an external signing service and require a human release approval.

## Before the first public download

- [ ] Run the full test suite on every packaging runner.
- [ ] Record the runner OS and CPU architecture in the Release notes.
- [ ] Test launch, scan, report export, guarded Trash, and quit on each supported OS.
- [ ] Confirm the app binds only to loopback and rejects an untrusted Host and Origin.
- [ ] Confirm every Release asset matches `SHA256SUMS.txt`.
- [ ] Publish known limitations and support status.
- [ ] Never describe an unsigned asset as a frictionless installer.

## macOS

1. Join the Apple Developer Program and obtain a Developer ID Application certificate owned by the publisher.
2. Import the certificate into a temporary CI keychain from protected secrets.
3. Decide which architectures are actually supported. A `macos-latest` build is not automatically universal.
4. Sign the `.app` bundle, its executable, and bundled native components with hardened runtime enabled.
5. Verify with `codesign --verify --deep --strict --verbose=2` and `spctl --assess --type execute --verbose=4`.
6. Submit the archive with `xcrun notarytool`, wait for success, staple the ticket, and assess again.
7. Test on a clean supported Mac that has not trusted a developer build previously.

## Windows

1. Obtain an organisation-appropriate Authenticode certificate or configure a managed signing service such as Azure Trusted Signing.
2. Sign the final `.exe` after packaging and add a trusted timestamp.
3. Verify the signature with `signtool verify /pa /v` on a clean Windows runner.
4. Test SmartScreen behaviour on a clean supported Windows installation. A valid new certificate may still have limited reputation.

## Linux

Linux desktop trust varies by distribution. Keep the SHA-256 manifest, document the build distribution and architecture, and consider distro-native packages only after those targets are tested.

## Release process

1. Update the version and changelog.
2. Merge only after CI passes.
3. Create an annotated `vX.Y.Z` tag on the intended commit.
4. Let `release-app` build and test every platform.
5. For trusted distribution, insert signing/notarisation steps **before** the archive steps and use protected environments.
6. Inspect the downloaded assets and checksum manifest before announcing the Release.
7. Confirm the website detects a compatible asset and still describes its signing status accurately.

Until every applicable signing step passes, the website and Release notes must continue to say “unsigned technical-evaluator build.”
