"""Project templates in one call (the agent's scaffold_project tool): a working starter the agent then builds on,
so "make me a site / API / bot" starts from files that already run on a phone with Termux."""
import os

from . import tools

NAMES = ["scaffold_project"]

TEMPLATES = {
    "web": {
        "about": "Static website: HTML, CSS and JavaScript (open index.html, or python -m http.server 8000)",
        "files": {
            "index.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" "
                          "content=\"width=device-width, initial-scale=1\">\n  <title>{name}</title>\n  <link rel=\"stylesheet\" "
                          "href=\"style.css\">\n</head>\n<body>\n  <main>\n    <h1>{name}</h1>\n    <p id=\"msg\">Ready.</p>\n"
                          "    <button id=\"go\">Click</button>\n  </main>\n  <script src=\"app.js\"></script>\n</body>\n</html>\n",
            "style.css": ":root { color-scheme: light dark; font-family: system-ui, sans-serif; }\nbody { margin: 0; }\n"
                         "main { max-width: 720px; margin: 0 auto; padding: 24px 16px; }\nbutton { padding: 10px 16px; "
                         "border-radius: 8px; border: 1px solid #8884; cursor: pointer; }\n",
            "app.js": "document.getElementById(\"go\").addEventListener(\"click\", () => {\n"
                      "  document.getElementById(\"msg\").textContent = \"Clicked at \" + new Date().toLocaleTimeString();\n});\n",
        },
    },
    "react": {
        "about": "React + Vite app (npm install, then npm run dev -- --host 127.0.0.1 --port 5173)",
        "files": {
            "package.json": "{\n  \"name\": \"{slug}\",\n  \"private\": true,\n  \"version\": \"0.1.0\",\n  \"type\": \"module\",\n"
                            "  \"scripts\": {\n    \"dev\": \"vite --host 127.0.0.1 --port 5173\",\n    \"build\": \"vite build\",\n"
                            "    \"preview\": \"vite preview --host 127.0.0.1 --port 4173\"\n  },\n  \"dependencies\": {\n"
                            "    \"react\": \"^18.3.1\",\n    \"react-dom\": \"^18.3.1\"\n  },\n  \"devDependencies\": {\n"
                            "    \"@vitejs/plugin-react\": \"^4.3.1\",\n    \"vite\": \"^5.4.0\"\n  }\n}\n",
            "vite.config.js": "import { defineConfig } from \"vite\";\nimport react from \"@vitejs/plugin-react\";\n\n"
                              "export default defineConfig({ plugins: [react()] });\n",
            "index.html": "<!doctype html>\n<html lang=\"en\">\n<head>\n  <meta charset=\"utf-8\">\n  <meta name=\"viewport\" "
                          "content=\"width=device-width, initial-scale=1\">\n  <title>{name}</title>\n</head>\n<body>\n"
                          "  <div id=\"root\"></div>\n  <script type=\"module\" src=\"/src/main.jsx\"></script>\n</body>\n</html>\n",
            "src/main.jsx": "import React from \"react\";\nimport { createRoot } from \"react-dom/client\";\nimport App from \"./App.jsx\";\n\n"
                            "createRoot(document.getElementById(\"root\")).render(<App />);\n",
            "src/App.jsx": "import { useState } from \"react\";\n\nexport default function App() {\n  const [count, setCount] = useState(0);\n"
                           "  return (\n    <main style={{ fontFamily: \"system-ui\", padding: 24 }}>\n      <h1>{name}</h1>\n"
                           "      <button onClick={() => setCount(count + 1)}>Clicked {count} times</button>\n    </main>\n  );\n}\n",
        },
    },
    "flask": {
        "about": "Python Flask web API (pip install flask, then python app.py)",
        "files": {
            "app.py": "from flask import Flask, jsonify\n\napp = Flask(__name__)\n\n\n@app.get(\"/\")\ndef home():\n"
                      "    return jsonify(app=\"{name}\", status=\"ok\")\n\n\nif __name__ == \"__main__\":\n"
                      "    app.run(host=\"127.0.0.1\", port=5000, debug=True)\n",
            "requirements.txt": "flask>=3.0\n",
            "test_app.py": "import unittest\n\nfrom app import app\n\n\nclass AppTest(unittest.TestCase):\n    def test_home(self):\n"
                           "        r = app.test_client().get(\"/\")\n        self.assertEqual(r.status_code, 200)\n\n\n"
                           "if __name__ == \"__main__\":\n    unittest.main()\n",
        },
    },
    "express": {
        "about": "Node.js Express API (npm install, then npm start)",
        "files": {
            "package.json": "{\n  \"name\": \"{slug}\",\n  \"version\": \"0.1.0\",\n  \"main\": \"server.js\",\n  \"scripts\": {\n"
                            "    \"start\": \"node server.js\"\n  },\n  \"dependencies\": {\n    \"express\": \"^4.19.2\"\n  }\n}\n",
            "server.js": "const express = require(\"express\");\n\nconst app = express();\napp.use(express.json());\n\n"
                         "app.get(\"/\", (req, res) => res.json({ app: \"{name}\", status: \"ok\" }));\n\n"
                         "app.listen(3000, \"127.0.0.1\", () => console.log(\"http://127.0.0.1:3000\"));\n",
        },
    },
    "telegram-bot": {
        "about": "Telegram bot in Python (pip install python-telegram-bot, set BOT_TOKEN, then python bot.py)",
        "files": {
            "bot.py": "import os\n\nfrom telegram import Update\nfrom telegram.ext import Application, CommandHandler, ContextTypes, "
                      "MessageHandler, filters\n\n\nasync def start(update: Update, context: ContextTypes.DEFAULT_TYPE):\n"
                      "    await update.message.reply_text(\"Hello from {name}!\")\n\n\n"
                      "async def echo(update: Update, context: ContextTypes.DEFAULT_TYPE):\n"
                      "    await update.message.reply_text(update.message.text)\n\n\ndef main():\n"
                      "    app = Application.builder().token(os.environ[\"BOT_TOKEN\"]).build()\n"
                      "    app.add_handler(CommandHandler(\"start\", start))\n"
                      "    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, echo))\n    app.run_polling()\n\n\n"
                      "if __name__ == \"__main__\":\n    main()\n",
            "requirements.txt": "python-telegram-bot>=21\n",
        },
    },
    "python-cli": {
        "about": "Python command-line tool with tests (python main.py --help; python -m unittest)",
        "files": {
            "main.py": "import argparse\n\n\ndef greet(name):\n    return \"Hello, %s!\" % name\n\n\ndef main(argv=None):\n"
                       "    parser = argparse.ArgumentParser(description=\"{name}\")\n"
                       "    parser.add_argument(\"name\", nargs=\"?\", default=\"world\")\n    args = parser.parse_args(argv)\n"
                       "    print(greet(args.name))\n\n\nif __name__ == \"__main__\":\n    main()\n",
            "test_main.py": "import unittest\n\nfrom main import greet\n\n\nclass GreetTest(unittest.TestCase):\n"
                            "    def test_greet(self):\n        self.assertEqual(greet(\"Ali\"), \"Hello, Ali!\")\n\n\n"
                            "if __name__ == \"__main__\":\n    unittest.main()\n",
        },
    },
}


def _slug(name):
    out = "".join(c if c.isalnum() else "-" for c in name.lower()).strip("-")
    return out or "app"


def install():
    @tools.tool("scaffold_project", "Create a working starter project from a template, then build on it: " +
                "; ".join("%s = %s" % (k, v["about"]) for k, v in TEMPLATES.items()) + ". Never overwrites files.",
                {"template": {"type": "string", "enum": sorted(TEMPLATES), "description": "the starter"},
                 "folder": tools._s("folder to create it in (relative; '.' for the project itself)"),
                 "name": tools._s("the app's display name")}, ["template"], "edit")
    def t_scaffold(ctx, template, folder=".", name=""):
        spec = TEMPLATES.get(str(template))
        if not spec:
            raise tools.ToolError("unknown template; choose one of: %s" % ", ".join(sorted(TEMPLATES)))
        name = (name or os.path.basename(os.path.abspath(os.path.join(ctx.root, folder or "."))) or "App").strip()[:60]
        base = tools.resolve(ctx, folder or ".", new=True)
        made, kept = [], []
        for rel, body in spec["files"].items():
            target = os.path.join(base, rel)
            if os.path.exists(target):
                kept.append(rel)
                continue
            text = body.replace("{name}", name).replace("{slug}", _slug(name))
            if hasattr(ctx, "before_change"):
                ctx.before_change(target)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(text)
            if hasattr(ctx, "changed"):
                ctx.changed.add(target)
            made.append(os.path.relpath(target, ctx.root))
        msg = "Created %s starter: %s" % (template, ", ".join(made) or "nothing new")
        if kept:
            msg += "\nKept existing: %s" % ", ".join(kept)
        msg += "\nNext: " + spec["about"]
        return msg, {"files": made, "template": template}
