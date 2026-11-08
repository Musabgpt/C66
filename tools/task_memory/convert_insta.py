"""InSTA-150K (CMU / Amazon, MIT) -> unified task-memory entries: a website and a task to do on it.

Input: the dataset's parquet files (data-for-agents/insta-150k-v1: domain, task). These are goals, not recorded
steps: the memory gives the agent the site and the goal, and the agent plans the steps on the live page.
Usage: python3 convert_insta.py <out.jsonl> <train.parquet> [test.parquet ...]
"""
import json
import re
import sys

import pyarrow.parquet as pq


def convert(out_path, paths):
    seen = set()
    n = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for path in paths:
            table = pq.read_table(path, columns=["domain", "task"])
            for row in table.to_pylist():
                domain = str(row["domain"] or "").strip().lower()
                task = re.sub(r"\s+", " ", str(row["task"] or "")).strip()
                if not domain or not task:
                    continue
                k = (domain, task.lower())
                if k in seen:
                    continue
                seen.add(k)
                n += 1
                out.write(json.dumps({"key": "insta.%d" % n, "src": "insta150k", "plat": "web", "app": domain,
                                      "pkg": domain, "goal": task, "exec": False, "start": "url"},
                                     ensure_ascii=False) + "\n")
    print("insta tasks", n)


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2:])
