# Contributing to Lemur

Thanks for helping. Lemur is a project of **Lemma**.

## Getting set up

```bash
pip install -r requirements.txt   # Python 3.12 recommended
npm install
npm start                         # run the app from source
npm test                          # Node tests, then Python tests
```

See [BUILD.md](BUILD.md) for packaging installers.

## Before you open a pull request

- **Run `npm test`.** CI runs the same suite on every push and pull request.
- **Add a test** when you fix a bug or change behaviour in `app.py`, `store.py`
  or `electron/`. Tests live in `tests/`: Python uses `unittest`, Node uses
  `node:test`. Python test modules must start with
  `from tests import sandbox` so they never touch a real user's data.
- **Translations:** user-visible text goes through `t('key')` or a
  `data-i18n` attribute, never a hard-coded string. Add new keys to every file
  in `locale/` so all languages keep the same set of keys.
- **Keep changes focused.** One fix or feature per pull request is easiest to
  review.

## A note on the frontend

The UI currently lives in a single large file, `templates/index.html`. Splitting
it into modules is welcome, but please do it in small, separate pull requests
rather than mixing a reorganisation with a feature.

## Licensing of contributions

Lemur is licensed under the GNU AGPL v3.0 or later. By submitting a
contribution, you agree that it is licensed under the same terms.

## Reporting bugs

Open an issue with your OS and Lemur version (Settings → About), what you did,
what you expected and what happened. For security problems, follow
[SECURITY.md](SECURITY.md) instead of opening a public issue.
