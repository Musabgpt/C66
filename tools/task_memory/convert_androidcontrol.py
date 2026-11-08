"""AndroidControl (Google Research, Apache-2.0) -> unified task-memory procedures.

Inputs (both public on Hugging Face, mirrors of the official GCS TFRecords):
  * ckg/AndroidControlParsed-20k: one row per step with the full goal, the app on screen, the accessibility tree
    of the screen (protobuf text format) and the action taken on it.
  * HarrytheOrange/parsed_AndroidControl (parsed_android_control.jsonl): the low-level instruction of every step.

Every click is turned from coordinates into what was tapped (the button's words, from the accessibility tree), so a
procedure can be replayed on another phone by label. Usage:
  python3 convert_androidcontrol.py <parquet dir> <parsed_android_control.jsonl> <out.jsonl>
"""
import glob
import json
import os
import re
import sys

import pyarrow.parquet as pq

import a11y_textproto as A

LAUNCHERS = {"pixel launcher", "launcher", "home", "one ui home", "launcher3", "quickstep", ""}
ENTER_WORDS = {"search", "enter", "go", "done", "next", "send", "ok", "return", "submit"}


def action_of(text):
    raw = text.split("!FUNCTIONCALL", 1)[-1].strip()
    try:
        d = json.loads(raw)
    except ValueError:
        return {"name": "unknown", "parameters": {}}
    return {"name": d.get("name", ""), "parameters": d.get("parameters") or {}}


def norm_space(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def load_low_level(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            d = json.loads(line)
            out[int(d["episode_id"])] = d
    return out


def convert(parquet_dir, jsonl_path, out_path):
    """The episodes come from the JSONL (every action and step instruction of all 15,283 demonstrations); the
    parquet rows (a large sample of the steps, with their screens) say what each click hit."""
    low = load_low_level(jsonl_path)
    seen = {}                                   # (episode, step) -> what the screen says about that step
    goals, apps, pkgs = {}, {}, {}
    files = sorted(glob.glob(os.path.join(parquet_dir, "*.parquet")))
    for path in files:
        pf = pq.ParquetFile(path)
        for rg in range(pf.num_row_groups):
            table = pf.read_row_group(rg, columns=["episode_id", "step_id", "messages"])
            for row in table.to_pylist():
                eid, sid = int(row["episode_id"]), int(row["step_id"])
                msgs = {m["role"]: m["content"] for m in row["messages"]}
                goal = norm_space(msgs.get("instruction"))
                if goal:
                    goals[eid] = goal
                app = norm_space(msgs.get("active_application"))
                if app and app.lower() not in LAUNCHERS:
                    apps.setdefault(eid, []).append(app)
                act = action_of(msgs.get("assistant", ""))
                ll = low.get(eid)
                want = (ll or {}).get("actions") or []
                nodes = A.parse(msgs.get("screen_description", ""))
                pkg = A.app_package(nodes)
                if pkg:
                    pkgs.setdefault(eid, []).append(pkg)
                if act["name"] not in ("click", "long_press") or sid >= len(want):
                    continue
                p = act["parameters"]
                w = want[sid]
                if w.get("action_type") != act["name"] or w.get("x") != p.get("x") or w.get("y") != p.get("y"):
                    continue                   # not the same step: never trust a misaligned label
                label, rid, kind = A.target(nodes, int(p.get("x", -1)), int(p.get("y", -1)))
                seen[(eid, sid)] = {"label": label, "rid": rid, "kind": kind}
        print("read", os.path.basename(path), "labelled steps", len(seen), flush=True)

    n = n_exec = 0
    with open(out_path, "w", encoding="utf-8") as out:
        for eid in sorted(low):
            ll = low[eid]
            actions = ll.get("actions") or []
            instructions = ll.get("step_instructions") or []
            goal = goals.get(eid) or norm_space(ll.get("goal"))
            if not actions or not goal:
                continue
            app_name = next((norm_space(a.get("app_name")) for a in actions if a.get("action_type") == "open_app"
                             and a.get("app_name")), "")
            if not app_name and apps.get(eid):
                app_name = max(set(apps[eid]), key=apps[eid].count)
            pkg = max(set(pkgs[eid]), key=pkgs[eid].count) if pkgs.get(eid) else ""
            steps, slots, executable = [], [], True
            starts_in_app = actions[0].get("action_type") != "open_app"
            for i, a in enumerate(actions):
                kind_ = a.get("action_type")
                hint = norm_space(instructions[i]) if i < len(instructions) else ""
                if kind_ == "open_app":
                    st = {"a": "open_app", "name": norm_space(a.get("app_name")) or app_name}
                    if pkg:
                        st["pkg"] = pkg
                elif kind_ in ("click", "long_press"):
                    s = seen.get((eid, i)) or {}
                    label = s.get("label", "")
                    if s.get("kind") == "key":
                        if label.lower() in ENTER_WORDS or not label:
                            st = {"a": "key", "name": "enter"}
                        else:
                            st = {"a": "type_key", "t": [label]}
                            executable = False
                    else:
                        st = {"a": "tap" if kind_ == "click" else "long_press"}
                        if label:
                            st["t"] = [label[:120]]
                        else:
                            st["xy"] = [int(a.get("x", 0)), int(a.get("y", 0))]
                            executable = False
                        if s.get("rid"):
                            st["id"] = s["rid"].split("/", 1)[-1][:80]
                        if s.get("kind") == "field":
                            st["field"] = True
                elif kind_ == "input_text":
                    text = str(a.get("text") or "")
                    st = {"a": "type", "text": text}
                    if text.strip() and text.strip().lower() in goal.lower():
                        if text not in slots:
                            slots.append(text)
                        st["text"] = "{%d}" % (slots.index(text) + 1)
                elif kind_ == "scroll":
                    st = {"a": "scroll", "direction": str(a.get("direction") or "down")}
                elif kind_ == "navigate_back":
                    st = {"a": "key", "name": "back"}
                elif kind_ == "navigate_home":
                    st = {"a": "key", "name": "home"}
                elif kind_ == "wait":
                    continue
                else:
                    executable = False
                    st = {"a": str(kind_)}
                if hint:
                    st["hint"] = hint[:200]
                if steps and {k: v for k, v in steps[-1].items() if k != "hint"} == \
                        {k: v for k, v in st.items() if k != "hint"} and st["a"] in ("tap", "type"):
                    continue
                steps.append(st)
            if not steps:
                continue
            if starts_in_app:
                executable = False
            pattern = goal
            for k, v in enumerate(slots):
                pattern = re.sub(re.escape(v), "{%d}" % (k + 1), pattern, flags=re.I)
            rec = {"key": "ac.%d" % eid, "src": "androidcontrol", "plat": "android", "app": app_name, "pkg": pkg,
                   "goal": goal, "steps": steps, "exec": bool(executable), "slots": slots,
                   "start": "app" if starts_in_app else "home"}
            if slots:
                rec["pattern"] = pattern
            n += 1
            n_exec += executable
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("episodes", n, "executable", n_exec, "labelled click steps", len(seen))


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2], sys.argv[3])
