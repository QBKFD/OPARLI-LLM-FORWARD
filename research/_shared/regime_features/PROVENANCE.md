# research/_shared/regime_features

Byte-identical copies of `regime_classifier/regime_features.py` and
`regime_classifier/regime_config.py`, as of tag `legacy-agents-v0` (last
changed in c9e1bb9). They were copied so research no longer depends on
`regime_classifier/`, which moves to `legacy/`.

- **Loader.** `research/vol_features/run_study.py::_import_feature_engine`
  loads these files (under the package name `regime_classifier`, so module
  identities are unchanged).
- **Dependents.** vol_features r1–r3, trend_features and regime_v2 reach them
  through that loader.
- **Do not edit.** Published research results were computed with exactly this
  code. When the copy was made, `existing_features()` on 2024-02-01..2024-04-30
  gave byte-identical output from both locations (86,816 rows × 13 features).
