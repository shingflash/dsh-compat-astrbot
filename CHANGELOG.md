# CHANGELOG

## Unreleased

- README rewritten as bilingual (English + 中文) with badges and a sectioned layout

## 1.0.2

- All in-code instruction text switched to English (comments, errors, protocol
  docs, CLI messages)
- Python interpreter auto-detection: `--python` -> `PYTHON` -> `py -3` ->
  `python3` -> `python`, each validated with `--version`
- Clear error when no Python 3 is found
- Verified end to end with Node v24.21.0 on real plugins

## 1.0.1

- Folded into 1.0.2 (not published separately): interpreter probe fix and
  documentation of the verified runtime

## 1.0.0

- First release: Node host + fake AstrBot API in Python
- Supports command / llm_tool / on_llm_request
- stdio JSON-lines protocol with `--probe` and `--rpc`
