# Code signing policy

## Current status

PneumoconiosisAnnotator is preparing an application to the [SignPath Foundation](https://signpath.org/). Acceptance, a signing account and a production certificate have not been obtained. Published Windows releases, including [0.3.1](https://github.com/deemoe404/slicer-pneumoconiosis/releases/tag/windows-v0.3.1-8634275-r14), are unsigned. A SHA-256 file is an integrity check, not a code signature.

If approved, releases signed through that program will carry this attribution:

> Free code signing provided by SignPath.io, certificate by SignPath Foundation.

The certificate publisher would be SignPath Foundation. Code signing does not establish clinical suitability, model accuracy, or antivirus clearance.

## Maintainer and responsibilities

| Role | Responsible maintainer |
| --- | --- |
| Author / committer | [deemoe404](https://github.com/deemoe404) |
| Reviewer of outside contributions | [deemoe404](https://github.com/deemoe404) |
| Release and signing approver | [deemoe404](https://github.com/deemoe404) |

This is currently a single-maintainer project. These roles do not imply independent review of the maintainer's own changes. Outside contributions must be reviewed before merging. Changes to build scripts, dependencies and signing configuration receive the same review as application code.

Maintainers must use multi-factor authentication for both GitHub and SignPath before signing is activated. Account MFA has to be confirmed by the account holder; its state is not established by publishing this policy. Each production signing request requires manual approval of the exact source revision, build and artifact.

## Signing scope and build origin

The proposed signing scope is our own Windows launcher `PneumoconiosisAnnotator.exe` and the project's Inno Setup installer and generated uninstaller. Third-party binaries, including Slicer, Python, ExtraMarkups and Python package DLLs, must retain their own provenance and any upstream signatures. They must not be re-signed as this project's own software.

The [Windows installer workflow](.github/workflows/windows-installer.yml) builds from a Git commit on a GitHub-hosted Windows runner. Pinned upstream downloads and model weights are checked against their recorded hashes. The build manifest records the source revision, dependency versions and payload file hashes. Installation, offline processing, persistence, reinstallation and uninstallation are checked before the current workflow creates a release draft.

Before production signing is enabled:

1. Confirm the eligibility of the complete distribution, including the unresolved items in [Third-party components](docs/third-party.md).
2. Link the authorized repository through SignPath's GitHub integration and restrict the exact files and their product/version metadata.
3. Submit artifacts uploaded by that GitHub workflow for origin verification. Local uploads and self-hosted builds are outside the proposed production signing path.
4. Sign our launcher before packaging, and arrange signing of the installer and uninstaller during packaging. Inno Setup's temporary executable copies also need the supported signing configuration; signing only the final outer EXE is insufficient.
5. Verify signatures, the expected certificate publisher and trusted timestamp; test the resulting installed package; compute the final installer SHA-256 after signing.
6. Obtain the maintainer's manual signing approval and record source/build/signing provenance with the release.

Inno Setup uses space-padded version string resources. Installed-package checks retain the raw resource values and compare the expected identity after removing only trailing space/NUL padding. The future SignPath metadata restrictions must account for that native padding while still restricting the product name and version; no post-build binary rewriting is part of this preparation.

No production signing workflow or account identifiers are configured yet. Current builds continue to be explicitly described as unsigned.

## Privacy and contact

The [Privacy policy](PRIVACY.md) covers local images, annotations, recordings, exports, logs, preparation downloads and the surrounding Slicer platform. The annotator workflow does not upload research data to a remote inference or analytics service.

Report signing problems through [GitHub Issues](https://github.com/deemoe404/slicer-pneumoconiosis/issues). Do not attach patient images, recordings, identifying paths, or unredacted logs to a public issue.

## Reference

This preparation follows the [SignPath Foundation conditions](https://signpath.org/terms.html) and [GitHub origin-verification documentation](https://docs.signpath.io/trusted-build-systems/github). SignPath decides eligibility and may require changes before granting access.
