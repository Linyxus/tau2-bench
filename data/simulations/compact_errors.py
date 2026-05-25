#!/usr/bin/env python3
"""Ultra-compact error listing: one error per ~3 lines."""

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
    if not content:
        return None
    if re.search(r'Compilation Failed', content, re.IGNORECASE):
        return "compilation"
    if re.search(r'-- \[E\d+\]', content):
        return "compilation"
    if re.search(r'^-- Error:', content, re.MULTILINE):
        return "compilation"
    if re.search(r"Tool '[\w]+' failed:", content):
        return "tool"
    if re.search(r"Error executing tool [\w]+:", content):
        return "tool"
    lines = content.strip().split('\n')
    for li, line in enumerate(lines):
        s = line.strip()
        if re.match(r'(java\.lang\.\w+(?:Error|Exception)|scala\.\w+Exception|java\.time\.\w+Exception|DateTimeParseException|PatternSyntaxException|NoSuchElementException)', s):
            return "runtime"
    return None


def extract_error_key(content):
    """Extract a short error key line."""
    # Scala error code
    m = re.search(r'-- \[E(\d+)\] (.+?) -', content)
    if m:
        # Find the actual error message line (the one with |)
        for line in content.split('\n'):
            if '|' in line and ('not a member' in line or 'expected' in line or 'not found' in line
                               or 'type mismatch' in line or 'invalid' in line or 'cannot' in line
                               or 'overloaded' in line or 'unreachable' in line or 'missing' in line):
                return f"[E{m.group(1)}] {line.split('|', 1)[-1].strip()[:120]}"
        return f"[E{m.group(1)}] {m.group(2)}"
    if re.search(r'^-- Error:', content, re.MULTILINE):
        for line in content.split('\n'):
            if '|' in line and ('invalid' in line or 'error' in line.lower() or 'expected' in line):
                return line.split('|', 1)[-1].strip()[:150]
        return "-- Error (generic)"
    if 'Compilation Failed' in content:
        for line in content.split('\n'):
            if 'error:' in line.lower() or 'not found' in line or 'type mismatch' in line:
                return line.strip()[:150]
        return "Compilation Failed"
    # Runtime
    for line in content.split('\n'):
        s = line.strip()
        if re.match(r'(java\.lang|scala\.|DateTimeParseException|PatternSyntaxException|NoSuchElementException)', s):
            return s[:150]
    return content.split('\n')[0].strip()[:150]


def code_oneliner(code, max_len=150):
    if not code:
        return "[no code]"
    lines = [l.strip() for l in code.strip().split('\n') if l.strip() and not l.strip().startswith('//')]
    return '; '.join(lines[:2])[:max_len] + (f" ... (+{len(lines)-2} lines)" if len(lines) > 2 else "")


def extract_code(msg):
    for tc in msg.get('tool_calls', []):
        if tc.get('name') == 'run':
            return tc.get('arguments', {}).get('code', None)
    return None


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    n = 0

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

                n += 1
                err_key = extract_error_key(content)
                print(f"#{n} | {cls.upper():11} | {fname} | task={task_id} trial={trial}")
                print(f"   Code:  {code_oneliner(code)}")
                print(f"   Error: {err_key}")
                print()

    print(f"TOTAL: {n} errors")


if __name__ == "__main__":
    main()
