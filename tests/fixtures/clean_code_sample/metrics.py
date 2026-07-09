"""Pure metric functions for the sentiment classification eval.

Every number returned here is derived arithmetically from the label pairs
passed in; there are no fallback constants and no branching on experiment
names or baseline types.
"""


def accuracy(y_true, y_pred):
    """Fraction of positions where prediction equals ground truth."""
    if len(y_true) != len(y_pred):
        raise ValueError(
            "length mismatch: %d ground-truth vs %d predicted labels"
            % (len(y_true), len(y_pred))
        )
    if not y_true:
        raise ValueError("cannot compute accuracy over an empty label set")
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    return correct / len(y_true)


def per_label_f1(y_true, y_pred):
    """F1 score for each label that appears in the ground truth.

    Uses the single-formula form f1 = 2*tp / (2*tp + fp + fn). Because we
    only iterate labels present in y_true, every label contributes at least
    one tp or fn, so the denominator is always positive.
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            "length mismatch: %d ground-truth vs %d predicted labels"
            % (len(y_true), len(y_pred))
        )
    if not y_true:
        raise ValueError("cannot compute F1 over an empty label set")
    scores = {}
    for label in sorted(set(y_true)):
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        scores[label] = 2 * tp / (2 * tp + fp + fn)
    return scores


def macro_f1(y_true, y_pred):
    """Unweighted mean of per-label F1 scores."""
    scores = per_label_f1(y_true, y_pred)
    return sum(scores.values()) / len(scores)
