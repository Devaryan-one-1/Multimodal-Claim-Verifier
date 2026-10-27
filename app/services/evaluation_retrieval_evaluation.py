"""Evaluation service exports; workflow implementation stays in evaluation/."""
from evaluation.retrieval_evaluation import (
    DEFAULT_DATASET_DIR,
    METRIC_NAMES,
    MRR_DEPTH,
    REPORT_SCHEMA_VERSION,
    evaluate_retrieval
)
