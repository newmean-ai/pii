# Security policy

## Reporting a vulnerability

Report security vulnerabilities privately, not in a public issue or pull request:

- on GitHub: [Report a vulnerability](https://github.com/newmean-ai/pii/security/advisories/new) (private advisory), or
- by e-mail to **hoonjae@newmean.ai**.

Include the affected package and version, the steps to reproduce, and the impact you expect. You get a first answer
within one week, and a confirmed vulnerability is fixed and released within 48 hours where possible (otherwise we tell
you the plan). We credit reporters in the advisory unless you ask us not to.

A result that misses personal data is a detection-quality bug, not a vulnerability: open a normal issue with made-up
values. It becomes a security issue when crafted input makes the SDK crash, read or write out of bounds, load a model
that fails its SHA-256 check, or send text over the network.

## Supported versions

Only the latest release of each package gets fixes.

## Scope

- the npm SDK (`@newmean-ai/pii`, `js/`)
- the C core shared by the native packages (`native/core/`)
- the Python package (`newmean-pii`, `python/`)
- the Swift package (`NewmeanPII`, `swift/`, `Package.swift`)
- the Android library (`ai.newmean:pii`, `android/`)
- the Flutter and Dart package (`newmean_pii`, `flutter/`)
- the release workflows (`.github/workflows/`)
- the site at pii.newmean.ai (`site/`)
- the leaderboard code (`leaderboard/`)

There is no bug bounty program.
