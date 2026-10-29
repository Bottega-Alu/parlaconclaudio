# Contributing to ParlaConClaudio

Thanks for helping make voice dictation on Windows more useful.

You do not need to write code to contribute:

- **Try the app:** tell us which Windows version, microphone and recognition engine you used, and what worked or got in the way.
- **Report a bug:** include the app version, steps to reproduce, expected result and actual result. Screenshots help; remove personal text and keys before posting.
- **Suggest an improvement:** describe the task you want to complete and how the change would help.
- **Improve a translation:** the interface supports Italian, English and Brazilian Portuguese. Catalogues live in `src/voice_bridge/locales/`.
- **Share the project:** send the repository link to someone who dictates text or works with coding agents. A GitHub star helps people discover it.

Use [Issues](https://github.com/Bottega-Alu/parlaconclaudio/issues) for bugs and ideas. Search existing issues first so related reports stay together. Never include API keys, private transcripts or unredacted configuration files.

## Code changes

1. Open an issue for substantial changes so the approach can be discussed.
2. Fork the repository and create a branch for one focused change.
3. Follow [the source setup in the README](README.md#build-from-source). The app and its UI tests require Windows.
4. Run `python -m pytest -q` in your development environment, and describe what you verified. For UI changes, include before/after screenshots and check light/dark themes and the affected interface languages.
5. Open a pull request explaining the problem, the resulting behavior and relevant validation.

Keep real user settings, recordings, credentials and local environments out of commits. Contributions are made under this repository's [MIT license](LICENSE).
