# Detection Benchmark Changelog Rewrite Design

## Goal

Replace every changelog addition made by `feat/detection-benchmark-eval` with one compact description of the final feature.

## Scope

The branch delta against `feat/review-pipeline-measurement` contains two changelog additions: the oversized detection-benchmark bullet and its explicit-`--trials 1` continuation. Replace both. Do not edit inherited `1.114.0` entries.

## Content

The replacement will state four public outcomes:

- scenario answer keys score detection quality;
- dispatch uses the configured reviewer and canonical model routing;
- `--trials N` and `--report-out` support repeatable comparison;
- invalid selections, rejected dispatches, and dispatch-only options outside dispatch mode exit nonzero.

Omit audit chronology, calibration anecdotes, fixture-by-fixture changes, intermediate defects, and implementation details that do not help a release reader understand the final capability.

## Verification

Compare `CHANGELOG.md` with the branch base. The final delta must contain exactly one added bullet in place of the current two additions, with no inherited changelog changes.
