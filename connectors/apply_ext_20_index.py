"""Codebase index and rules (Cursor-style): a project map at the start of every thread, and rules files read
automatically: .cursorrules, .cursor/rules/*.md(c), .musabai/rules.md (besides AGENTS.md / CLAUDE.md)."""
import shutil


def apply(src, package, replace):
    shutil.copyfile(src / "project_map.py", package / "project_map.py")
    ext = package / "extensions.py"
    replace(ext, 'INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", os.path.join(".newal", "INSTRUCTIONS.md"))',
            'INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", os.path.join(".newal", "INSTRUCTIONS.md"),\n'
            '                     ".cursorrules", os.path.join(".musabai", "rules.md"))')
    replace(ext, "            if os.path.isfile(f) and f not in out:\n                out.append(f)\n    return out\n",
            "            if os.path.isfile(f) and f not in out:\n                out.append(f)\n"
            "    rules = os.path.join(os.path.abspath(root), \".cursor\", \"rules\")\n"
            "    if os.path.isdir(rules):\n"
            "        for name in sorted(os.listdir(rules)):\n"
            "            f = os.path.join(rules, name)\n"
            "            if name.endswith((\".md\", \".mdc\")) and os.path.isfile(f) and f not in out:\n"
            "                out.append(f)\n"
            "    return out\n")
    replace(package / "agent.py", "                idx = extensions.skills_index(self.skills)\n",
            "                if not self._tiny():\n"
            "                    from . import project_map\n"
            "                    parts.append(project_map.block(s.root))\n"
            "                idx = extensions.skills_index(self.skills)\n")
