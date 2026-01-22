#!/usr/bin/env python3
"""
Parse Test Results from Multiple Frameworks

Unifies JSON output from Jest, PHPUnit, and Playwright into a standard format
that review agents can easily consume.

Usage:
    ./parse-test-results.py /tmp/test-results/*.json

Output: Unified JSON to stdout with:
    - Overall pass/fail status
    - Count of passed/failed tests
    - Details of failures with messages and locations
    - Framework-agnostic format

Implements: Proposal #5 (Rich Feedback Loops) - Phase 1
"""

import json
import sys
import os
from typing import Dict, List, Any

def parse_jest_results(filepath: str) -> Dict[str, Any]:
    """Parse Jest JSON output."""
    with open(filepath) as f:
        data = json.load(f)

    failures = []

    if 'testResults' in data:
        for test in data.get('testResults', []):
            if test.get('status') == 'failed':
                failures.append({
                    'test': test.get('name', 'Unknown test'),
                    'message': '\n'.join(test.get('failureMessages', [])),
                    'location': test.get('location', 'Unknown'),
                    'framework': 'Jest'
                })

    return {
        'framework': 'Jest',
        'success': data.get('success', False),
        'total': data.get('numTotalTests', 0),
        'passed': data.get('numPassedTests', 0),
        'failed': data.get('numFailedTests', 0),
        'failures': failures
    }

def parse_phpunit_results(filepath: str) -> Dict[str, Any]:
    """Parse PHPUnit JSON output."""
    with open(filepath) as f:
        data = json.load(f)

    failures = []

    # PHPUnit JSON format varies, handle common structures
    if 'event' in data and data['event'] == 'test':
        # Log format
        for test in data.get('tests', []):
            if test.get('status') in ['error', 'failure']:
                failures.append({
                    'test': test.get('name', 'Unknown test'),
                    'message': test.get('message', ''),
                    'location': f"{test.get('file', '')}:{test.get('line', '')}",
                    'framework': 'PHPUnit'
                })

    total = data.get('numTests', 0)
    failed = len(failures)

    return {
        'framework': 'PHPUnit',
        'success': failed == 0,
        'total': total,
        'passed': total - failed,
        'failed': failed,
        'failures': failures
    }

def parse_playwright_results(filepath: str) -> Dict[str, Any]:
    """Parse Playwright JSON output."""
    with open(filepath) as f:
        # Playwright outputs NDJSON (newline-delimited), take last line
        lines = f.readlines()

        # Find the summary line (usually last non-empty line)
        for line in reversed(lines):
            line = line.strip()
            if line:
                try:
                    data = json.loads(line)
                    if 'stats' in data:
                        break
                except json.JSONDecodeError:
                    continue

    failures = []

    # Extract failures from test results
    for line in lines:
        try:
            entry = json.loads(line.strip())
            if entry.get('type') == 'testEnd' and entry.get('test', {}).get('outcome') == 'unexpected':
                test_info = entry.get('test', {})
                failures.append({
                    'test': test_info.get('title', 'Unknown'),
                    'message': test_info.get('error', {}).get('message', ''),
                    'location': f"{test_info.get('file', '')}:{test_info.get('line', '')}",
                    'framework': 'Playwright'
                })
        except (json.JSONDecodeError, KeyError):
            continue

    stats = data.get('stats', {})

    return {
        'framework': 'Playwright',
        'success': stats.get('unexpected', 0) == 0,
        'total': stats.get('expected', 0) + stats.get('unexpected', 0),
        'passed': stats.get('expected', 0),
        'failed': stats.get('unexpected', 0),
        'failures': failures
    }

def unify_results(results_list: List[Dict]) -> Dict[str, Any]:
    """Combine multiple framework results into unified format."""
    unified = {
        'overall_success': all(r['success'] for r in results_list),
        'frameworks': {},
        'summary': {
            'total': 0,
            'passed': 0,
            'failed': 0
        },
        'all_failures': []
    }

    for result in results_list:
        framework = result['framework']

        unified['frameworks'][framework] = {
            'success': result['success'],
            'total': result['total'],
            'passed': result['passed'],
            'failed': result['failed']
        }

        unified['summary']['total'] += result['total']
        unified['summary']['passed'] += result['passed']
        unified['summary']['failed'] += result['failed']

        unified['all_failures'].extend(result['failures'])

    return unified

def main():
    """Parse all test result files and output unified JSON."""
    if len(sys.argv) < 2:
        print("Usage: parse-test-results.py <test-result-files...>", file=sys.stderr)
        sys.exit(1)

    results = []

    for filepath in sys.argv[1:]:
        if not os.path.exists(filepath):
            print(f"Warning: {filepath} not found", file=sys.stderr)
            continue

        filename = os.path.basename(filepath)

        try:
            if 'jest' in filename.lower():
                results.append(parse_jest_results(filepath))
            elif 'phpunit' in filename.lower():
                results.append(parse_phpunit_results(filepath))
            elif 'playwright' in filename.lower():
                results.append(parse_playwright_results(filepath))
            else:
                print(f"Warning: Unknown format for {filename}", file=sys.stderr)

        except Exception as e:
            print(f"Error parsing {filename}: {e}", file=sys.stderr)
            continue

    if not results:
        print("Error: No test results parsed", file=sys.stderr)
        sys.exit(1)

    # Unify and output
    unified = unify_results(results)
    print(json.dumps(unified, indent=2))

    # Exit with failure code if tests failed
    sys.exit(0 if unified['overall_success'] else 1)

if __name__ == '__main__':
    main()
