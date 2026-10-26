"""Expose only usable free models on Android without removing user-saved desktop models.

The backend registry retains every existing spec. Android picker availability is
derived from actual free endpoint credentials; a catalog entry is not proof
that the user is authenticated or has quota.
"""


def apply(target, replace):
    pkg = target / "desktop/newal_code"
    model = pkg / "models.py"
    service = pkg / "service.py"
    ui = pkg / "ui/app.js"

    replace(model,
            '    for s in nvidia_models.models():\n        out.setdefault(s["id"], s)\n    return out\n',
            '    for s in nvidia_models.models():\n        out.setdefault(s["id"], s)\n'
            '    from . import provider_pool\n'
            '    for s in provider_pool.FREE_POOL:\n        out.setdefault(s["id"], dict(s))\n'
            '    # Official Kilo Gateway Auto Free offers anonymous, rate-limited access.\n'
            '    # The user should not send confidential data through public free routing.\n'
            '    out.setdefault("kilo-public/auto-free", {\n'
            '        "id": "kilo-public/auto-free", "name": "Auto Free · No key (public)",\n'
            '        "provider": "openai", "base_url": "https://ai-gateway.kilo.ai/api/v1",\n'
            '        "model": "kilo-auto/free", "free": True,\n'
            '        "capabilities": ["text", "tools", "streaming", "reasoning"],\n'
            '        "about": "Public free tier; prompts may be logged by providers",\n'
            '    })\n'
            '    return out\n')

    # The selected NVIDIA and other free providers must use the same protected
    # credential source as failover; NVIDIA has NIM-specific payload handling.
    replace(model,
            '    if spec.get("secret_id") == "nvidia" or spec.get("preset") == "nvidia":\n'
            '        from . import provider_pool\n'
            '        return provider_pool._key(dict(spec, secret_id="nvidia"))\n',
            '    if spec.get("secret_id"):\n'
            '        from . import provider_pool\n'
            '        return provider_pool._key(spec)\n'
            '    if spec.get("preset") == "nvidia":\n'
            '        from . import provider_pool\n'
            '        return provider_pool._key(dict(spec, secret_id="nvidia"))\n')

    replace(model,
            '    return Client(spec, providers.OpenAICompat(base, _key(spec), spec.get("headers")),\n'
            '                  spec.get("model") or spec["id"])\n',
            '    if base.rstrip("/") in ("https://integrate.api.nvidia.com/v1",\n'
            '                             "https://ai-gateway.kilo.ai/api/v1"):\n'
            '        from . import provider_pool\n'
            '        credential = _key(spec)\n'
            '        if base.rstrip("/") == "https://integrate.api.nvidia.com/v1" and not credential:\n'
            '            raise ValueError("NVIDIA not connected: enter a new API key under MusabAI Connections → NVIDIA")\n'
            '        client = provider_pool.provider(dict(spec, api_key=credential))\n'
            '        return Client(spec, client, spec.get("model") or spec["id"])\n'
            '    credential = _key(spec)\n'
            '    if spec.get("secret_id") and not credential:\n'
            '        raise ValueError("Connect the selected free provider in MusabAI Connections first")\n'
            '    return Client(spec, providers.OpenAICompat(base, credential, spec.get("headers")),\n'
            '                  spec.get("model") or spec["id"])\n')

    replace(model,
            '    if os.environ.get("ANTHROPIC_API_KEY"):\n',
            '    if os.environ.get("NEWAL_PHONE_URL") and "kilo-public/auto-free" in reg:\n'
            '        return reg["kilo-public/auto-free"]\n'
            '    if os.environ.get("ANTHROPIC_API_KEY"):\n')

    replace(model,
            '    if os.environ.get("ANTHROPIC_API_KEY"):\n',
            '    from . import provider_pool\n'
            '    for free in provider_pool.FREE_POOL:\n'
            '        if provider_pool._key(free):\n'
            '            return free\n'
            '    if os.environ.get("ANTHROPIC_API_KEY"):\n')

    # Preserve the user's other models; send readiness separately, never send
    # raw provider secrets back to the WebView.
    replace(service,
            '                        "file": spec.get("file", "") if spec.get("provider") == "local" else ""})\n',
            '                        "file": spec.get("file", "") if spec.get("provider") == "local" else "",\n'
            '                        "free": bool(spec.get("free")) if spec.get("provider") != "local" else True,\n'
            '                        "ready": self._musab_model_ready(spec)})\n')

    replace(service,
            '    def model_listing(self):\n',
            '    @staticmethod\n'
            '    def _musab_model_ready(spec):\n'
            '        if spec.get("provider") == "local":\n'
            '            return bool(spec.get("downloaded"))\n'
            '        if spec.get("secret_id"):\n'
            '            from . import provider_pool\n'
            '            return bool(provider_pool._key(spec))\n'
            '        return bool(spec.get("api_key") or spec.get("base_url") and not\n'
            '                    (spec.get("api_key_env") or spec.get("api_key_envs")))\n'
            '\n'
            '    def model_listing(self):\n')

    # Only free and already configured endpoints appear as usable Android
    # models. The provider setup panel still shows providers not yet linked.
    replace(ui,
            '      const remote = S.models.models.filter(m => m.provider !== "local");\n',
            '      const phone = !!window.NewAlPhone;\n'
            '      const remote = S.models.models.filter(m => m.provider !== "local" &&\n'
            '        (!phone || (m.free === true && m.ready === true)));\n')
    replace(ui,
            '      remote.forEach(m => entries.push({ value: m.id, title: m.name, sub: m.provider, checked: model === m.id }));\n',
            '      remote.forEach(m => entries.push({ value: m.id, title: m.name,\n'
            '        sub: phone ? "Free · configured" : m.provider, checked: model === m.id }));\n'
            '      if (phone && S.models.models.some(m => m.provider !== "local" && m.free && !m.ready))\n'
            '        entries.push({ value: "__connect__", title: "Connect free models · ربط النماذج المجانية",\n'
            '          sub: "NVIDIA, Groq, Gemini or OpenRouter: key required" });\n')
    replace(ui,
            '    const other = d.models.filter(m => !m.catalog && !own.includes(m));\n',
            '    const other = d.models.filter(m => !m.catalog && !own.includes(m) &&\n'
            '      (!window.NewAlPhone || m.provider === "local" || m.free === true));\n')
    replace(ui,
            '      oth.appendChild(h("div", "card", \'<div class="grow"><div class="name">\' + esc(m.name) + \'</div><div class="desc">\' + esc(m.id) + " · " + esc(m.provider || "") + "</div></div>" +\n'
            '        \'<button class="btn small" data-use="\' + esc(m.id) + \'">Use</button>\'));\n',
            '      oth.appendChild(h("div", "card", \'<div class="grow"><div class="name">\' + esc(m.name) + \'</div><div class="desc">\' + esc(m.id) +\n'
            '        (m.free ? " · FREE" : "") + (m.ready === false ? " · needs API key" : " · available") +\n'
            '        "</div></div>" + (m.ready === false ? \'<button class="btn small" data-free-connect="1">Connect</button>\' :\n'
            '          \'<button class="btn small" data-use="\' + esc(m.id) + \'">Use</button>\')));\n')

    replace(ui,
            '    body.querySelectorAll("[data-use]").forEach(b => b.onclick = () => { setOpt("model", b.dataset.use); closeModal(); });\n',
            '    body.querySelectorAll("[data-use]").forEach(b => b.onclick = () => { setOpt("model", b.dataset.use); closeModal(); });\n'
            '    body.querySelectorAll("[data-free-connect]").forEach(b => b.onclick = () => {\n'
            '      if (window.NewAlPhone && NewAlPhone.configureNvidia) NewAlPhone.configureNvidia();\n'
            '      else toast("Configure this free provider using the Connections panel");\n'
            '    });\n')
