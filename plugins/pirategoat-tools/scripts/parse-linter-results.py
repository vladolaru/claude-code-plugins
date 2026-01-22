#!/usr/bin/env python3
"""
Parse Linter Results from Multiple Tools

Unifies JSON output from ESLint and PHPCS into a standard format
that review agents can easily consume.

Usage:
    ./parse-linter-results.py /tmp/lint-results/*.json

Output: Unified JSON to stdout with:
    - Overall pass/fail status
    - Count of violations by severity
    - Details of violations with file/line/rule
    - Linter-agnostic format

Implements: Proposal #5 (Rich Feedback Loops) - Phase 2
"""

import json
import sys
import os
from typing import Dict, List, Any

def parse_eslint_results(filepath: str) -> Dict[str, Any]:
    """Parse ESLint JSON output."""
    with open(filepath) as f:
        data = json.load(f)

    violations = []
    error_count = 0
    warning_count = 0

    for file_result in data:
        file_path = file_result.get('filePath', 'Unknown')

        for message in file_result.get('messages', []):
            severity = 'error' if message.get('severity', 1) == 2 else 'warning'

            if severity == 'error':
                error_count += 1
            else:
                warning_count += 1

            violations.append({
                'file': file_path,
                'line': message.get('line', 0),
                'column': message.get('column', 0),
                'severity': severity,
                'rule': message.get('ruleId', 'unknown'),
                'message': message.get('message', ''),
                'linter': 'ESLint'
            })

    return {
        'linter': 'ESLint',
        'pass': error_count == 0,  # Warnings don't fail the build
        'total_violations': error_count + warning_count,
        'errors': error_count,
        'warnings': warning_count,
        'violations': violations
    }

def parse_phpcs_results(filepath: str) -> Dict[str, Any]:
    """Parse PHPCS JSON output."""
    with open(filepath) as f:
        data = json.load(f)

    violations = []
    error_count = 0
    warning_count = 0

    files = data.get('files', {})

    for file_path, file_data in files.items():
        for message in file_data.get('messages', []):
            severity = 'error' if message.get('type', 'ERROR') == 'ERROR' else 'warning'

            if severity == 'error':
                error_count += 1
            else:
                warning_count += 1

            violations.append({
                'file': file_path,
                'line': message.get('line', 0),
                'column': message.get('column', 0),
                'severity': severity,
                'rule': message.get('source', 'unknown'),
                'message': message.get('message', ''),
                'linter': 'PHPCS'
            })

    return {
        'linter': 'PHPCS',
        'pass': error_count == 0,  # Warnings don't fail the build
        'total_violations': error_count + warning_count,
        'errors': error_count,
        'warnings': warning_count,
        'violations': violations
    }

def unify_results(results_list: List[Dict]) -> Dict[str, Any]:
    """Combine multiple linter results into unified format."""
    unified = {
        'overall_pass': all(r['pass'] for r in results_list),
        'linters': {},
        'summary': {
            'total_violations': 0,
            'errors': 0,
            'warnings': 0
        },
        'all_violations': []
    }

    for result in results_list:
        linter = result['linter']

        unified['linters'][linter] = {
            'pass': result['pass'],
            'total_violations': result['total_violations'],
            'errors': result['errors'],
            'warnings': result['warnings']
        }

        unified['summary']['total_violations'] += result['total_violations']
        unified['summary']['errors'] += result['errors']
        unified['summary']['warnings'] += result['warnings']

        unified['all_violations'].extend(result['violations'])

    # Sort violations by severity (errors first) then by file
    unified['all_violations'].sort(
        key=lambda v: (0 if v['severity'] == 'error' else 1, v['file'], v['line'])
    )

    return unified

def main():
    """Parse all linter result files and output unified JSON."""
    if len(sys.argv) < 2:
        print("Usage: parse-linter-results.py <linter-result-files...>", file=sys.stderr)
        sys.exit(1)

    results = []

    for filepath in sys.argv[1:]:
        if not os.path.exists(filepath):
            print(f"Warning: {filepath} not found", file=sys.stderr)
            continue

        filename = os.path.basename(filepath)

        try:
            if 'eslint' in filename.lower():
                results.append(parse_eslint_results(filepath))
            elif 'phpcs' in filename.lower():
                results.append(parse_phpcs_results(filepath))
            else:
                print(f"Warning: Unknown linter format for {filename}", file=sys.stderr)

        except Exception as e:
            print(f"Error parsing {filename}: {e}", file=sys.stderr)
            continue

    if not results:
        print("Error: No linter results parsed", file=sys.stderr)
        sys.exit(1)

    # Unify and output
    unified = unify_results(results)
    print(json.dumps(unified, indent=2))

    # Exit with failure code if linters found errors (not warnings)
    sys.exit(0 if unified['overall_pass'] else 1)

if __name__ == '__main__':
    main()
