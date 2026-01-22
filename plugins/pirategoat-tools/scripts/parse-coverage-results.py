#!/usr/bin/env python3
"""
Parse Coverage Results from Multiple Frameworks

Unifies coverage output from Jest and PHPUnit into a standard format
that review agents can easily consume.

Usage:
    ./parse-coverage-results.py <coverage-directory>

Output: Unified JSON to stdout with:
    - Overall coverage percentage
    - Coverage by framework (line, branch, function)
    - Uncovered files and line ranges
    - Coverage gaps for review focus

Implements: Proposal #5 (Rich Feedback Loops) - Phase 3
"""

import json
import sys
import os
import xml.etree.ElementTree as ET
from typing import Dict, List, Any, Optional

def parse_jest_coverage(directory: str) -> Optional[Dict[str, Any]]:
    """Parse Jest coverage-summary.json."""
    summary_file = os.path.join(directory, 'jest-coverage-summary.json')

    if not os.path.exists(summary_file):
        return None

    with open(summary_file) as f:
        data = json.load(f)

    total = data.get('total', {})

    files_with_gaps = []
    for filepath, file_data in data.items():
        if filepath == 'total':
            continue

        line_cov = file_data.get('lines', {}).get('pct', 100)
        if line_cov < 80:  # Flag files below 80% coverage
            uncovered_lines = []

            # Try to read detailed line coverage from lcov
            # (This is a simplified version - full lcov parsing would be more complex)
            files_with_gaps.append({
                'file': filepath,
                'line_coverage': line_cov,
                'branch_coverage': file_data.get('branches', {}).get('pct', 0),
                'function_coverage': file_data.get('functions', {}).get('pct', 0)
            })

    return {
        'framework': 'Jest',
        'overall': {
            'line': total.get('lines', {}).get('pct', 0),
            'branch': total.get('branches', {}).get('pct', 0),
            'function': total.get('functions', {}).get('pct', 0),
            'statement': total.get('statements', {}).get('pct', 0)
        },
        'files_below_threshold': files_with_gaps
    }

def parse_phpunit_coverage(directory: str) -> Optional[Dict[str, Any]]:
    """Parse PHPUnit clover.xml coverage."""
    clover_file = os.path.join(directory, 'phpunit-coverage.xml')

    if not os.path.exists(clover_file):
        return None

    tree = ET.parse(clover_file)
    root = tree.getroot()

    # Get overall metrics from project
    project = root.find('.//project')
    if project is None:
        return None

    metrics = project.find('metrics')
    if metrics is None:
        return None

    total_statements = int(metrics.get('statements', 0))
    covered_statements = int(metrics.get('coveredstatements', 0))
    total_conditionals = int(metrics.get('conditionals', 0))
    covered_conditionals = int(metrics.get('coveredconditionals', 0))

    line_coverage = (covered_statements / total_statements * 100) if total_statements > 0 else 0
    branch_coverage = (covered_conditionals / total_conditionals * 100) if total_conditionals > 0 else 0

    # Find files with low coverage
    files_with_gaps = []

    for file_elem in root.findall('.//file'):
        filename = file_elem.get('name', '')
        file_metrics = file_elem.find('metrics')

        if file_metrics is not None:
            file_statements = int(file_metrics.get('statements', 0))
            file_covered = int(file_metrics.get('coveredstatements', 0))
            file_coverage = (file_covered / file_statements * 100) if file_statements > 0 else 0

            if file_coverage < 80:  # Flag files below 80%
                # Collect uncovered line numbers
                uncovered_lines = []
                for line_elem in file_elem.findall('.//line'):
                    if line_elem.get('type') == 'stmt' and line_elem.get('count') == '0':
                        uncovered_lines.append(int(line_elem.get('num')))

                files_with_gaps.append({
                    'file': filename,
                    'line_coverage': file_coverage,
                    'uncovered_lines': uncovered_lines[:50]  # Limit to first 50 for readability
                })

    return {
        'framework': 'PHPUnit',
        'overall': {
            'line': line_coverage,
            'branch': branch_coverage
        },
        'files_below_threshold': files_with_gaps
    }

def unify_results(results_list: List[Dict]) -> Dict[str, Any]:
    """Combine multiple coverage results into unified format."""
    unified = {
        'frameworks': {},
        'overall_coverage': 0.0,
        'all_files_below_threshold': []
    }

    total_line_coverage = 0
    framework_count = 0

    for result in results_list:
        framework = result['framework']

        unified['frameworks'][framework] = result['overall']
        total_line_coverage += result['overall']['line']
        framework_count += 1

        unified['all_files_below_threshold'].extend(result['files_below_threshold'])

    # Calculate average line coverage across frameworks
    if framework_count > 0:
        unified['overall_coverage'] = total_line_coverage / framework_count

    # Sort files by coverage (lowest first)
    unified['all_files_below_threshold'].sort(
        key=lambda f: f.get('line_coverage', 100)
    )

    return unified

def main():
    """Parse all coverage result files and output unified JSON."""
    if len(sys.argv) < 2:
        print("Usage: parse-coverage-results.py <coverage-directory>", file=sys.stderr)
        sys.exit(1)

    coverage_dir = sys.argv[1]

    if not os.path.isdir(coverage_dir):
        print(f"Error: {coverage_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    results = []

    # Try to parse Jest coverage
    jest_result = parse_jest_coverage(coverage_dir)
    if jest_result:
        results.append(jest_result)
        print(f"✅ Parsed Jest coverage: {jest_result['overall']['line']:.1f}% line coverage", file=sys.stderr)

    # Try to parse PHPUnit coverage
    phpunit_result = parse_phpunit_coverage(coverage_dir)
    if phpunit_result:
        results.append(phpunit_result)
        print(f"✅ Parsed PHPUnit coverage: {phpunit_result['overall']['line']:.1f}% line coverage", file=sys.stderr)

    if not results:
        print("Error: No coverage results found", file=sys.stderr)
        print(f"Looked in: {coverage_dir}/", file=sys.stderr)
        print("  - jest-coverage-summary.json (Jest)", file=sys.stderr)
        print("  - phpunit-coverage.xml (PHPUnit Clover)", file=sys.stderr)
        sys.exit(1)

    # Unify and output
    unified = unify_results(results)
    print(json.dumps(unified, indent=2))

    sys.exit(0)

if __name__ == '__main__':
    main()
