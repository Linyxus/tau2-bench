#!/usr/bin/env python3
"""Analyze Scala errors: group by error type, show unique patterns, and output complete listing."""

import json
import re
import os
from collections import Counter, defaultdict

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
    """Classify error. Returns 'compilation', 'runtime', 'tool', or None."""
    if not content:
        return None

    # 1) Compilation errors - explicit markers
    if re.search(r'Compilation Failed', content, re.IGNORECASE):
        return "compilation"
    if re.search(r'-- \[E\d+\]', content):
        # Check it's actually an error, not just a warning in successful output
        # Errors have "Error:" in the bracket description
        if re.search(r'-- \[E\d+\] .*(Error|Warning)', content):
            return "compilation"
        return "compilation"
    # "-- Error:" without [E...] code
    if re.search(r'^-- Error:', content, re.MULTILINE):
        return "compilation"

    # 2) Tool errors: RuntimeException from a Tool call
    if re.search(r"Tool '[\w]+' failed:", content):
        return "tool"
    if re.search(r"Error executing tool [\w]+:", content):
        return "tool"

    # 3) Scala runtime errors - actual exceptions thrown at runtime
    # Must have a stack trace or exception at the START of the output (not in source code)
    # Real runtime exceptions start with the exception class name
    if re.match(r'\s*(java\.lang\.\w+(?:Error|Exception)|scala\.\w+Exception|\w+Exception):', content):
        return "runtime"
    # Or have a stack trace pattern: "at package.Class.method(File.scala:line)"
    # with an exception before it
    lines = content.strip().split('\n')
    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        # Exception at start of a line (not inside source code)
        if re.match(r'(java\.lang\.\w+(?:Error|Exception)|scala\.\w+Exception|java\.time\.\w+Exception)', stripped):
            # Check there's a stack trace after
            if line_idx + 1 < len(lines) and re.match(r'\s+at ', lines[line_idx + 1]):
                return "runtime"
            return "runtime"
        # Exception with message
        if re.match(r'(java\.lang\.\w+(?:Error|Exception)|DateTimeParseException|PatternSyntaxException|NoSuchElementException)', stripped):
            return "runtime"

    # 4) Check for warnings-only (not errors)
    if re.search(r'-- Warning:', content) or re.search(r'warning found', content):
        # Warnings are not errors; skip unless there's also an error
        if not re.search(r'error', content, re.IGNORECASE):
            return None

    return None


def extract_error_type_detail(content, cls):
    """Extract a more detailed error category."""
    if cls == 'compilation':
        m = re.search(r'-- \[E(\d+)\] (.+?) -', content)
        if m:
            return f"[E{m.group(1)}] {m.group(2)}"
        if re.search(r'^-- Error:', content, re.MULTILINE):
            if 'invalid string interpolation' in content:
                return "invalid string interpolation"
            return "generic -- Error"
        if 'Compilation Failed' in content:
            if 'invalid string interpolation' in content:
                return "invalid string interpolation"
            if 'not found' in content.lower():
                return "not found (compilation)"
            if 'type mismatch' in content.lower():
                return "type mismatch"
            return "Compilation Failed (other)"
        return "other compilation"
    elif cls == 'runtime':
        m = re.search(r'(java\.lang\.\w+(?:Error|Exception))', content)
        if m:
            return m.group(1)
        m = re.search(r'(\w+Exception)', content)
        if m:
            return m.group(1)
        return "other runtime"
    return "unknown"


def extract_code(msg):
    for tc in msg.get('tool_calls', []):
        if tc.get('name') == 'run':
            return tc.get('arguments', {}).get('code', None)
    return None


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    all_errors = []
    compilation_detail_counter = Counter()
    runtime_detail_counter = Counter()

    for fname in FILES:
        fpath = os.path.join(base_dir, fname)
        if not os.path.exists(fpath):
            continue

        with open(fpath) as f:
            data = json.load(f)

        for sim in data.get('simulations', []):
            task_id = sim.get('task_id', sim.get('id', '?'))
            trial = sim.get('trial', '?')
            messages = sim.get('messages', [])

            for i, msg in enumerate(messages):
                if msg.get('role') != 'tool':
                    continue
                content = msg.get('content', '')
                cls = classify_error(content)
                if cls is None or cls == 'tool':
                    continue

                code = None
                for j in range(i - 1, -1, -1):
                    if messages[j].get('role') == 'assistant':
                        code = extract_code(messages[j])
                        if code is not None:
                            break

                detail = extract_error_type_detail(content, cls)
                if cls == 'compilation':
                    compilation_detail_counter[detail] += 1
                else:
                    runtime_detail_counter[detail] += 1

                all_errors.append({
                    'file': fname,
                    'task_id': task_id,
                    'trial': trial,
                    'cls': cls,
                    'detail': detail,
                    'code': code,
                    'error_content': content,
                })

    # === COMPLETE LISTING ===
    print("=" * 100)
    print("COMPLETE LISTING OF ALL SCALA COMPILATION AND RUNTIME ERRORS")
    print("=" * 100)
    print()

    for idx, e in enumerate(all_errors, 1):
        print(f"{'='*100}")
        print(f"ERROR #{idx}  [{e['cls'].upper()}]  {e['detail']}")
        print(f"  File:    {e['file']}")
        print(f"  Task:    {e['task_id']} (trial {e['trial']})")
        print(f"  --- Scala Code ---")
        if e['code']:
            for line in e['code'].strip().split('\n'):
                print(f"    {line}")
        else:
            print(f"    [no code found]")
        print(f"  --- Error Output ---")
        err_lines = e['error_content'].strip().split('\n')
        for line in err_lines[:60]:
            print(f"    {line}")
        if len(err_lines) > 60:
            print(f"    ... ({len(err_lines) - 60} more lines)")
        print()

    # === SUMMARY ===
    print()
    print("=" * 100)
    print("SUMMARY")
    print("=" * 100)
    n_comp = sum(1 for e in all_errors if e['cls'] == 'compilation')
    n_rt = sum(1 for e in all_errors if e['cls'] == 'runtime')
    print(f"\nTotal compilation errors: {n_comp}")
    print(f"Total runtime errors:     {n_rt}")
    print(f"Grand total:              {len(all_errors)}")

    print(f"\n--- Compilation Error Breakdown ---")
    for detail, count in compilation_detail_counter.most_common():
        print(f"  {count:>5}  {detail}")

    print(f"\n--- Runtime Error Breakdown ---")
    for detail, count in runtime_detail_counter.most_common():
        print(f"  {count:>5}  {detail}")

    # Per-file breakdown
    file_counts = defaultdict(lambda: {"compilation": 0, "runtime": 0})
    for e in all_errors:
        file_counts[e['file']][e['cls']] += 1

    print(f"\n--- Per-File Breakdown ---")
    print(f"  {'File':<50} {'Comp':>8} {'Runtime':>8} {'Total':>8}")
    print(f"  {'-'*50} {'-'*8} {'-'*8} {'-'*8}")
    for fname in FILES:
        fc = file_counts.get(fname, {"compilation": 0, "runtime": 0})
        t = fc['compilation'] + fc['runtime']
        print(f"  {fname:<50} {fc['compilation']:>8} {fc['runtime']:>8} {t:>8}")


if __name__ == "__main__":
    main()
