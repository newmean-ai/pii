# Contributing

Thank you for helping. newmean-pii is maintained by one person at [newmean.ai](https://newmean.ai), so the rules below
keep things simple and predictable. [한국어](CONTRIBUTING.ko.md)

## Response and release schedule

- Every issue and pull request gets a first answer **within one week**.
- Releases go out **once a week**, bundling the changes accepted that week.
- Urgent fixes (security, or results that are wrong in a way that can leak personal data) are released **within 48 hours**.

## Issues

- **Bugs**: use the bug template. Name the package and version (npm, PyPI, Swift, Android, Flutter), the model size, a
  minimal input that shows the problem, and what you expected. Please replace real personal data with made-up values.
- **Feature requests**: welcome, but the scope is kept small on purpose. A request may be closed as out of scope with a
  short reason; that is not a judgment on the idea.
- **Security problems**: never in a public issue. See [SECURITY.md](SECURITY.md).

## Pull requests

This public repository is published from a private development repository, so **pull requests are not merged with the
GitHub button**. Instead:

1. If the change is accepted, the maintainer applies it in the development repository with your credit
   (`Co-authored-by: <you>` in the commit).
2. It ships in the next weekly release, and the pull request is closed with a link to that release.

For anything larger than a small fix, open an issue first so we can agree on the approach before you spend time on it.

Checklist:

- Keep one change per pull request, with tests when behavior changes.
- Run the tests of the package you touched:

  | package | command |
  |---|---|
  | `js/` | `npm ci && npm test` |
  | `native/core/` | `make test` |
  | `python/` | see [python/README.md](python/README.md#development) (`uv build`, then `pytest -q`) |
  | `swift/` | `swift test` (repository root) |
  | `android/` | `./gradlew :pii:testDebugUnitTest` |
  | `flutter/` | `dart test` |

- The C core in `native/core/` is shared by the Python, Swift, Android and Flutter packages; a change there must keep
  all of them in agreement with the npm package.
- Training data and the data generator are not in this repository; pull requests cannot change the models.

## License of contributions

By submitting a contribution you agree that it is licensed under the [Apache License 2.0](LICENSE), the same license as
the project (Apache-2.0 section 5), and that you have the right to submit it.

## Conduct

Be kind and specific. Harassment or personal attacks lead to removal from the project's spaces.
