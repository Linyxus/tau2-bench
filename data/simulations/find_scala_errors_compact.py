#!/usr/bin/env python3
"""Compact version: prints each error as a short entry."""

import json
import re
import os

FILES = [
    "deepseek-v3.2_scala_airline.json",
    "deepseek-v3.2_scala_retail.json",
    "gemini3flash_airline_scala.json",
    "gpt-oss-120b_retail_scala.json",
    "gptoss120b_airline_scala_trial2.json",
    "gptoss120b_airline_scala.json",
    "haiku_airline_scala.json",
    "minimax-m2.5_airline_scala_trial2.json",
    "minimax-m2.5_airline_scala_trial2.json.v0",
    "minimax-m2.5_airline_scala.json",
    "minimax-m2.5_retail_scala_feb24.json",
    "minimax-m2.5_retail_scala_trial2.json",
    "minimax-m2.5_retail_scala_trial3.json",
    "minimax-m2.5_retail_scala_trial4.json",
    "minimax-m2.5_retail_scala.json",
]


def classify_error(content):
    if not content:
        return None
    if re.search(r'Compilation Failed', content, re.IGNORECASE):
        return "compilation"
    if re.search(r'-- \[E\d+\]', content):
        return "compilation"
    if re.search(r'^\d+ \|', content, re.MULTILINE) and re.search(r'error:', content, re.IGNORECASE):
        if not re.search(r'Exception|at [\w.$]+\(', content):
            return "compilation"
    if re.search(r"Tool '[\w]+' failed:", content):
        return "tool"
    if re.search(r"Error executing tool [\w]+:", content):
        return "tool"
    if re.search(r'Exception', content) or re.search(r'java\.lang\.\w+Error', content):
        return "runtime"
    if re.search(r'error:', content, re.IGNORECASE):
        if re.search(r'not found:|type mismatch|is not a member|cannot be applied|overloaded method', content):
            return "compilation"
    return None


def extract_error_summary(content, max_len=200):
    """Extract a short summary of the error."""
    lines = content.strip().split('\n')
    # For compilation errors, find the line with "error:" or the main message
    for line in lines:
        line = line.strip()
        if 'Compilation Failed' in line:
            continue
        if re.search(r'error:|Error:|not found:|type mismatch', line):
            return line[:max_len]
        if re.search(r'Exception:', line) or re.search(r'Exception$', line):
            return line[:max_len]
    # fallback: first non-empty line
    for line in lines[:5]:
        if line.strip():
            return line.strip()[:max_len]
    return content[:max_len]


def extract_code(msg):
    for tc in msg.get('tool_calls', []):
        if tc.get('name') == 'run':
            return tc.get('arguments', {}).get('code', None)
    return None


def code_summary(code, max_lines=3, max_len=120):
    """Return first few lines of code as summary."""
    if not code:
        return "[no code]"
    lines = [l for l in code.strip().split('\n') if l.strip()]
    result = '; '.join(lines[:max_lines])
    if len(lines) > max_lines:
        result += f" ... (+{len(lines)-max_lines} lines)"
    return result[:max_len*2]


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    n = 0
    counts = {"compilation": 0, "runtime": 0, "tool": 0}
    file_counts = {}

    for fname in FILES:
        fpath = os.path.join(base_dir, fname)
        if not os.path.exists(fpath):
            print(f"WARNING: File not found: {fname}")
            continue

        with open(fpath) as f:
            data = json.load(f)

        fc = {"compilation": 0, "runtime": 0, "tool": 0}

        for sim in data.get('simulations', []):
            task_id = sim.get('task_id', sim.get('id', '?'))
            trial = sim.get('trial', '?')
            messages = sim.get('messages', [])

            for i, msg in enumerate(messages):
                if msg.get('role') != 'tool':
                    continue
                content = msg.get('content', '')
                cls = classify_error(content)
                if cls is None:
                    continue

                counts[cls] += 1
                fc[cls] += 1
                n += 1

                if cls in ("compilation", "runtime"):
                    code = None
                    for j in range(i - 1, -1, -1):
                        if messages[j].get('role') == 'assistant':
                            code = extract_code(messages[j])
                            if code is not None:
                                break

                    err_summary = extract_error_summary(content)
                    print(f"[{n}] {cls.upper()} | {fname} | task={task_id} trial={trial}")
                    print(f"  Code: {code_summary(code)}")
                    print(f"  Error: {err_summary}")
                    print()

        file_counts[fname] = fc

    print("=" * 80)
    print("SUMMARY")
    print(f"Total: {n} errors (compilation={counts['compilation']}, "
          f"runtime={counts['runtime']}, tool={counts['tool']})")
    print()
    print(f"  {'File':<50} {'Comp':>6} {'RT':>6} {'Tool':>6} {'Total':>6}")
    print(f"  {'-'*50} {'-'*6} {'-'*6} {'-'*6} {'-'*6}")
    for fname in FILES:
        fc = file_counts.get(fname)
        if fc is None:
            print(f"  {fname:<50} [not found]")
        else:
            t = sum(fc.values())
            print(f"  {fname:<50} {fc['compilation']:>6} {fc['runtime']:>6} {fc['tool']:>6} {t:>6}")


if __name__ == "__main__":
    main()
