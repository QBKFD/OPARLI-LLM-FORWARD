"""
Statistical jump model (Nystrup, Lindström & Madsen 2020) with causal labels.

    minimize  Σ_t ||x_t − μ_{s_t}||²  +  λ · Σ_t 1[s_t ≠ s_{t−1}]

Fitting alternates (1) the exact state sequence for fixed centroids (Viterbi
dynamic program) and (2) centroid = mean of assigned rows, from several
k-means++ initialisations. The best objective is kept.

Causal labels use the forward pass of the dynamic program only: at each t the
label is the state minimising the accumulated cost using rows up to t. No
backtracking, so a label never changes when later data arrives.

Old regime_classifier bugs this module avoids by design: no RF layer, no
fillna(0) (the caller passes only complete rows), no return-based naming.
"""

from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True)
def _viterbi(C, lam):
    """Offline optimal path for costs C (T, K). Returns labels and objective."""
    T, K = C.shape
    V = C[0].copy()
    ptr = np.empty((T, K), np.int64)
    newV = np.empty(K)
    offset = 0.0
    for t in range(1, T):
        m = 0
        for k in range(1, K):
            if V[k] < V[m]:
                m = k
        vm = V[m]
        for k in range(K):
            stay = V[k]
            sw = vm + lam
            if stay <= sw:
                ptr[t, k] = k
                newV[k] = C[t, k] + stay
            else:
                ptr[t, k] = m
                newV[k] = C[t, k] + sw
        mn = newV[0]
        for k in range(1, K):
            if newV[k] < mn:
                mn = newV[k]
        for k in range(K):
            V[k] = newV[k] - mn
        offset += mn
    last = 0
    for k in range(1, K):
        if V[k] < V[last]:
            last = k
    obj = V[last] + offset
    labels = np.empty(T, np.int64)
    labels[T - 1] = last
    for t in range(T - 1, 0, -1):
        labels[t - 1] = ptr[t, labels[t]]
    return labels, obj


@njit(cache=True)
def _forward(C, lam):
    """Causal labels: argmin of the accumulated forward cost at each t."""
    T, K = C.shape
    V = C[0].copy()
    labels = np.empty(T, np.int64)
    best = 0
    for k in range(1, K):
        if V[k] < V[best]:
            best = k
    labels[0] = best
    newV = np.empty(K)
    for t in range(1, T):
        m = 0
        for k in range(1, K):
            if V[k] < V[m]:
                m = k
        vm = V[m]
        for k in range(K):
            stay = V[k]
            sw = vm + lam
            newV[k] = C[t, k] + (stay if stay <= sw else sw)
        best = 0
        for k in range(1, K):
            if newV[k] < newV[best]:
                best = k
        mn = newV[best]
        for k in range(K):
            V[k] = newV[k] - mn
        labels[t] = best
    return labels


def sqdist(X: np.ndarray, mu: np.ndarray) -> np.ndarray:
    return ((X[:, None, :] - mu[None, :, :]) ** 2).sum(axis=2)


def _kmeanspp(X, K, rng):
    mu = [X[rng.integers(len(X))]]
    for _ in range(1, K):
        d = sqdist(X, np.array(mu)).min(axis=1)
        p = d / d.sum() if d.sum() > 0 else None
        mu.append(X[rng.choice(len(X), p=p)])
    return np.array(mu, dtype=float)


class JumpModel:
    def __init__(self, K: int = 3, lam: float = 1.0, n_init: int = 10, max_iter: int = 100,
                 seed: int = 0):
        self.K, self.lam, self.n_init, self.max_iter, self.seed = K, float(lam), n_init, max_iter, seed
        self.centroids_ = None
        self.labels_ = None          # offline labels of the training rows
        self.objective_ = np.inf

    def fit(self, X: np.ndarray) -> "JumpModel":
        X = np.ascontiguousarray(X, dtype=float)
        if not np.isfinite(X).all():
            raise ValueError("JumpModel.fit got non-finite values; pass complete rows only")
        rng = np.random.default_rng(self.seed)
        for _ in range(self.n_init):
            mu = _kmeanspp(X, self.K, rng)
            labels = None
            for _ in range(self.max_iter):
                new, _obj = _viterbi(sqdist(X, mu), self.lam)
                if labels is not None and np.array_equal(new, labels):
                    break
                labels = new
                for k in range(self.K):
                    idx = labels == k
                    if idx.any():
                        mu[k] = X[idx].mean(axis=0)
                    else:                                   # empty state: farthest point
                        mu[k] = X[np.argmax(sqdist(X, mu)[np.arange(len(X)), labels])]
            C = sqdist(X, mu)
            obj = C[np.arange(len(X)), labels].sum() + self.lam * np.count_nonzero(np.diff(labels))
            if obj < self.objective_:
                self.objective_, self.centroids_, self.labels_ = obj, mu.copy(), labels.copy()
        return self

    def offline_labels(self, X: np.ndarray) -> np.ndarray:
        return _viterbi(sqdist(np.asarray(X, float), self.centroids_), self.lam)[0]

    def causal_labels(self, X: np.ndarray) -> np.ndarray:
        return _forward(sqdist(np.asarray(X, float), self.centroids_), self.lam)


def order_by(values: np.ndarray, labels: np.ndarray, K: int) -> np.ndarray:
    """Map raw state ids to ranks 0..K-1 by the mean of `values` per state (ascending)."""
    means = np.array([values[labels == k].mean() if (labels == k).any() else np.inf for k in range(K)])
    rank = np.empty(K, np.int64)
    rank[np.argsort(means, kind="stable")] = np.arange(K)
    return rank


def mean_duration(labels: np.ndarray) -> float:
    labels = np.asarray(labels)
    if len(labels) == 0:
        return np.nan
    return len(labels) / (1 + np.count_nonzero(np.diff(labels)))
