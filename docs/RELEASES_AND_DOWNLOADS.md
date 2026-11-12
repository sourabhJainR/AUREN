# AUREN releases, downloads and branch lifecycle

## Source of truth

- main is the canonical, continuously available source branch.
- The active mainBranchRule ruleset protects the default branch against deletion and non-fast-forward updates. Keep this rule active.
- Feature branches are disposable delivery branches. After a pull request is successfully merged, the Delete merged feature branches workflow deletes the source branch when it belongs to this repository.
- Fork-owned branches are not deleted by AUREN. The workflow never deletes the default branch.

## Download channels

- Stable versions: immutable semantic-version GitHub Releases, for example v1.0.6. The release workflow verifies the canonical VERSION and package metadata before publishing.
- Current main: every successful push to main builds a source-pinned portable bundle and publishes an immutable prerelease tagged main-<12 character commit>.
- Older versions: remain available as GitHub Releases. Publishing a new main snapshot never replaces or removes a stable version.

Every release includes auren-portable.zip, install.sh, install.ps1 and SHA256SUMS. The portable bundle is verified before publication and records the exact source commit. The downloads page reads the public GitHub Releases API, so it lists current and historical releases without a manually maintained version table.

## Install a selected build

Linux/macOS:

    AUREN_VERSION=v1.0.6 curl -fsSL https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.sh | bash

Windows PowerShell:

    $env:AUREN_VERSION = "v1.0.6"
    irm https://raw.githubusercontent.com/sourabhJainR/AUREN/main/install.ps1 | iex

Use the exact tag shown on the downloads page. Main snapshots are prereleases and should be used when the newest merged code is needed before a stable release is cut.

## Repository setting

GitHub also provides a native Automatically delete head branches setting under Settings → General → Pull Requests. Enable it when repository administration access is available. The merge-close workflow in this repository provides the same cleanup behavior for same-repository feature branches and keeps deletion independent of that UI setting.