#!/usr/bin/env python3
"""
Parse Security Scanner Results

Unifies JSON output from Semgrep into a standard format
that review agents can easily consume.

Usage:
    ./parse-security-results.py <security-results-directory>

Output: Unified JSON to stdout with:
    - Overall pass/fail status
    - Count of findings by severity
    - Details of security issues with file/line/rule
    - Scanner-agnostic format

Implements: Proposal #5 (Rich Feedback Loops) - Phase 4
"""

import json
import sys
import os
from typing import Dict, List, Any, Optional

def parse_semgrep_results(directory: str) -> Optional[Dict[str, Any]]:
    """Parse Semgrep JSON output."""
    semgrep_file = os.path.join(directory, 'semgrep-results.json')

    if not os.path.exists(semgrep_file):
        return None

    with open(semgrep_file) as f:
        data = json.load(f)

    findings = []
    severity_count = {'high': 0, 'medium': 0, 'low': 0, 'info': 0}

    for result in data.get('results', []):
        # Semgrep severity mapping
        severity = result.get('extra', {}).get('severity', 'INFO').lower()

        # Map Semgrep severity to standard
        if severity in ['error', 'high']:
            mapped_severity = 'high'
            severity_count['high'] += 1
        elif severity in ['warning', 'medium']:
            mapped_severity = 'medium'
            severity_count['medium'] += 1
        elif severity == 'low':
            mapped_severity = 'low'
            severity_count['low'] += 1
        else:
            mapped_severity = 'info'
            severity_count['info'] += 1

        findings.append({
            'file': result.get('path', 'Unknown'),
            'line': result.get('start', {}).get('line', 0),
            'column': result.get('start', {}).get('col', 0),
            'severity': mapped_severity,
            'rule': result.get('check_id', 'unknown'),
            'message': result.get('extra', {}).get('message', result.get('check_id', 'Security issue detected')),
            'scanner': 'Semgrep',
            'cwe': result.get('extra', {}).get('metadata', {}).get('cwe', [])
        })

    return {
        'scanner': 'Semgrep',
        'pass': severity_count['high'] == 0,  # Only high severity blocks
        'total_findings': len(findings),
        'by_severity': severity_count,
        'findings': findings
    }

def unify_results(results_list: List[Dict]) -> Dict[str, Any]:
    """Combine multiple scanner results into unified format."""
    unified = {
        'overall_pass': all(r['pass'] for r in results_list),
        'scanners': {},
        'summary': {
            'total_findings': 0,
            'high': 0,
            'medium': 0,
            'low': 0,
            'info': 0
        },
        'all_findings': []
    }

    for result in results_list:
        scanner = result['scanner']

        unified['scanners'][scanner] = {
            'pass': result['pass'],
            'total_findings': result['total_findings'],
            'by_severity': result['by_severity']
        }

        unified['summary']['total_findings'] += result['total_findings']
        for severity in ['high', 'medium', 'low', 'info']:
            unified['summary'][severity] += result['by_severity'].get(severity, 0)

        unified['all_findings'].extend(result['findings'])

    # Sort findings by severity (high first) then by file
    severity_order = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}
    unified['all_findings'].sort(
        key=lambda f: (severity_order.get(f['severity'], 4), f['file'], f['line'])
    )

    return unified

def main():
    """Parse all security scanner result files and output unified JSON."""
    if len(sys.argv) < 2:
        print("Usage: parse-security-results.py <security-results-directory>", file=sys.stderr)
        sys.exit(1)

    results_dir = sys.argv[1]

    if not os.path.isdir(results_dir):
        print(f"Error: {results_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    results = []

    # Try to parse Semgrep results
    semgrep_result = parse_semgrep_results(results_dir)
    if semgrep_result:
        results.append(semgrep_result)
        print(f"✅ Parsed Semgrep: {semgrep_result['total_findings']} findings ({semgrep_result['by_severity']['high']} high)", file=sys.stderr)

    if not results:
        print("Error: No security scan results found", file=sys.stderr)
        print(f"Looked in: {results_dir}/", file=sys.stderr)
        print("  - semgrep-results.json (Semgrep)", file=sys.stderr)
        sys.exit(1)

    # Unify and output
    unified = unify_results(results)
    print(json.dumps(unified, indent=2))

    # Exit with failure code if high severity findings exist
    sys.exit(0 if unified['overall_pass'] else 1)

if __name__ == '__main__':
    main()
