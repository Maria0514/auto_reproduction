"""Evaluate sentiment-classification predictions against ground truth.

Reads two JSONL files, aligns examples by id, computes accuracy and
macro-F1 from the actual label pairs, and writes a summary.json. All
reported numbers are computed from the input files at run time.

Usage:
    python evaluate.py \
        --ground-truth data/ground_truth.jsonl \
        --predictions data/predictions.jsonl \
        --output outputs/summary.json
"""

import argparse
import json
from pathlib import Path

from metrics import accuracy, macro_f1, per_label_f1


def load_jsonl(path):
    """Load a JSONL file into a dict keyed by example id."""
    records = {}
    for line_no, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if "id" not in record or "label" not in record:
            raise ValueError("%s line %d: record needs 'id' and 'label'" % (path, line_no))
        if record["id"] in records:
            raise ValueError("%s line %d: duplicate id %r" % (path, line_no, record["id"]))
        records[record["id"]] = record["label"]
    return records


def align(truth, preds):
    """Pair up labels by id; every id must exist on both sides."""
    missing = sorted(set(truth) - set(preds))
    extra = sorted(set(preds) - set(truth))
    if missing or extra:
        raise ValueError(
            "id mismatch between files: missing predictions for %r, unexpected ids %r"
            % (missing, extra)
        )
    ids = sorted(truth)
    return [truth[i] for i in ids], [preds[i] for i in ids]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    here = Path(__file__).resolve().parent
    parser.add_argument("--ground-truth", default=str(here / "data" / "ground_truth.jsonl"))
    parser.add_argument("--predictions", default=str(here / "data" / "predictions.jsonl"))
    parser.add_argument("--output", default=str(here / "outputs" / "summary.json"))
    args = parser.parse_args()

    truth = load_jsonl(args.ground_truth)
    preds = load_jsonl(args.predictions)
    y_true, y_pred = align(truth, preds)

    summary = {
        "experiment_name": "clean_sentiment_eval",
        "num_examples": len(y_true),
        "accuracy": accuracy(y_true, y_pred),
        "macro_f1": macro_f1(y_true, y_pred),
        "per_label_f1": per_label_f1(y_true, y_pred),
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
