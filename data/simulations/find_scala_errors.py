#!/usr/bin/env python3
"""Find all Scala compilation and runtime errors in simulation JSON files.

Classifies errors into:
- compilation error: Scala code failed to compile
- scala runtime error: Scala code compiled but threw an exception NOT from a tool call
- tool error: A domain tool was called successfully but returned an error (e.g., "Reservation not found")
"""

import json
import re
import os
import sys

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
    """Classify error in tool response content.

    Returns: "compilation error", "scala runtime error", "tool error", or None
    """
    if not content:
        return None

    # 1) Compilation errors
    if re.search(r'Compilation Failed', content, re.IGNORECASE):
        return "compilation error"
    if re.search(r'-- \[E\d+\]', content):
        return "compilation error"
    # Scala 3 compilation error patterns without "Compilation Failed" header
    if re.search(r'^\d+ \|', content, re.MULTILINE) and re.search(r'error:', content, re.IGNORECASE):
        # Has line-numbered source and "error:" -> likely compilation
        if not re.search(r'Exception|at [\w.$]+\(', content):
            return "compilation error"

    # 2) Tool errors: RuntimeException from a Tool call
    if re.search(r"Tool '[\w]+' failed:", content):
        return "tool error"
    if re.search(r"Error executing tool [\w]+:", content):
        return "tool error"

    # 3) Scala runtime errors (exceptions NOT from tool calls)
    if re.search(r'Exception', content) or re.search(r'java\.lang\.\w+Error', content):
        return "scala runtime error"

    # 4) Other error-like patterns
    if re.search(r'error:', content, re.IGNORECASE):
        # Might be a compilation error we missed
        if re.search(r'not found:|type mismatch|is not a member|cannot be applied|overloaded method', content):
            return "compilation error"
        return None  # Generic "error:" in tool output text, not necessarily an error

    return None


def extract_code_from_assistant(msg):
    """Extract Scala code from an assistant message's tool_calls."""
    tool_calls = msg.get('tool_calls', [])
    if not tool_calls:
        return None
    for tc in tool_calls:
        if tc.get('name') == 'run':
            args = tc.get('arguments', {})
            return args.get('code', None)
    return None


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    total_errors = 0
    error_counts = {"compilation error": 0, "scala runtime error": 0, "tool error": 0}
    file_error_counts = {}

    for fname in FILES:
        fpath = os.path.join(base_dir, fname)
        if not os.path.exists(fpath):
            print(f"WARNING: File not found: {fname}")
            continue

        with open(fpath) as f:
            data = json.load(f)

        simulations = data.get('simulations', [])
        file_errors = {"compilation error": 0, "scala runtime error": 0, "tool error": 0}

        for sim in simulations:
            task_id = sim.get('task_id', sim.get('id', '?'))
            trial = sim.get('trial', '?')
            messages = sim.get('messages', [])

            for i, msg in enumerate(messages):
                if msg.get('role') != 'tool':
                    continue
                content = msg.get('content', '')
                if not content:
                    continue

                classification = classify_error(content)
                if classification is None:
                    continue

                # Find preceding assistant message with code
                code = None
                for j in range(i - 1, -1, -1):
                    if messages[j].get('role') == 'assistant':
                        code = extract_code_from_assistant(messages[j])
                        if code is not None:
                            break

                total_errors += 1
                error_counts[classification] += 1
                file_errors[classification] += 1

                # Only print compilation and scala runtime errors in detail
                # Tool errors are domain-level, less interesting
                if classification in ("compilation error", "scala runtime error"):
                    print("=" * 80)
                    print(f"ERROR #{total_errors}  [{classification.upper()}]")
                    print(f"  File:           {fname}")
                    print(f"  Task ID:        {task_id} (trial {trial})")
                    print(f"  --- Scala Code Submitted ---")
                    if code:
                        for line in code.strip().split('\n'):
                            print(f"    {line}")
                    else:
                        print(f"    [No code found in preceding assistant message]")
                    print(f"  --- Error Message ---")
                    err_lines = content.strip().split('\n')
                    for line in err_lines[:50]:
                        print(f"    {line}")
                    if len(err_lines) > 50:
                        print(f"    ... ({len(err_lines) - 50} more lines)")
                    print()

        file_error_counts[fname] = file_errors

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total errors found: {total_errors}")
    for k, v in error_counts.items():
        print(f"  {k}: {v}")
    print()
    print("Errors per file:")
    print(f"  {'File':<50} {'Compile':>8} {'ScalaRT':>8} {'ToolErr':>8} {'Total':>8}")
    print(f"  {'-'*50} {'-'*8} {'-'*8} {'-'*8} {'-'*8}")
    for fname in FILES:
        fe = file_error_counts.get(fname)
        if fe is None:
            print(f"  {fname:<50} [file not found]")
        else:
            t = sum(fe.values())
            print(f"  {fname:<50} {fe['compilation error']:>8} {fe['scala runtime error']:>8} {fe['tool error']:>8} {t:>8}")


if __name__ == "__main__":
    main()
