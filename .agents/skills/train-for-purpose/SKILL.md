---
name: train-for-purpose
description: Train, tune, or evaluate models in this repository while keeping the model aligned with the intended detection task and checking for misleading results.
---

# Train for the intended purpose

Use this skill whenever a task runs, changes, or evaluates model training in this repository.

## Intended task

This repository's default product task is HTTP request security classification: predict whether a request is normal (`label=0`) or an attack (`label=1`). For the numbered text datasets, `normal.txt` supplies label 0 and `attack.txt` supplies label 1. Keep this target meaning stable. Do not switch to regression, change label meanings, or optimize for a different outcome unless the user explicitly asks.

## Before training

- Identify the requested input dataset and confirm it represents the stated task. The default pair is `data/normal.txt` and `data/attack.txt`; explicitly supplied local or S3 inputs take precedence.
- For CSV input, inspect the target column and label values. Do not assume a column named `label` has the expected meaning without checking.
- Check that both classes exist and that records have usable request content. If the data or target conflicts with the requested purpose, explain the mismatch and stop before training rather than silently relabeling or substituting data.
- Preserve user-selected task, inputs, models, and limits. Ask only if a missing choice materially changes what the model is meant to detect.

## Train and evaluate

- Treat the agent or LLM as a hyperparameter planner. It may select parameters within the configured search space; it must not redefine labels, drop difficult categories, or change the success measure to make the result look better.
- Use the existing train/validation split for trial selection. Keep evaluation examples out of fitting and tuning.
- Review both aggregate metrics and category results. This repo runs category tests on raw and URL-encoded examples; report weak categories and failures even when aggregate accuracy is high.
- The configured `category_target_accuracy` is the acceptance threshold for category testing. Do not claim the target is met unless the trainer's report says so.
- Prefer the existing classification metrics (accuracy, F1, precision, recall) when assessing the default binary detection task. Call out class imbalance or other conditions that make accuracy alone misleading.

## Report the outcome

State the dataset and target meaning, task, selected model, relevant validation and category-test results, whether the configured target was met, and where artifacts were saved. Distinguish measured results from assumptions. Do not describe a model as production-ready based only on a successful training run.
