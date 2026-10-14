# Figures

Regenerate with `backend/venv/bin/python3 research/vol_features/run_study.py`.

## Clustering and correlations, 2021–2023 (descriptive)

Dendrogram (average linkage on 1 − |Spearman ρ|; the dashed line is the 0.2 cut)
and the correlation heatmap ordered by the clustering. The new features are in bold.

![full period](figures/corr_full_2021_2023.png)

## Clustering and correlations, initial training window (2021, used for D2)

![2021](figures/corr_train_1_to_2021-12-31.png)

## Stability: cluster co-membership across the 8 training windows

Each cell is the share of the 8 expanding training windows in which the two
features fall in the same cluster at the 0.2 cut.

![co-membership](figures/comembership_train_windows.png)
