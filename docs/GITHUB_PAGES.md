# GitHub Pages contract

AUREN's public documentation site is `https://sourabhJainR.github.io/AUREN/`.

## Required entrypoints

`site/index.html` and `site/404.html` are the canonical static payload for the GitHub Actions deployment workflow.

`docs/index.html` and `docs/404.html` are retained as compatibility entrypoints because the repository previously published Pages from `main/docs`. Removing them without first verifying the repository Pages source can turn a working site into a 404.

## Cleanup rule

Treat the four entrypoints as deployment artifacts, not stale documentation. Repository cleanup must preserve them until the Pages source is explicitly confirmed to be GitHub Actions and the compatibility contract is deliberately removed.

The deployment workflow checks all four files before uploading the `site/` payload.
