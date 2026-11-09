#!/data/data/com.termux/files/usr/bin/bash
# =====================================================================
# setup.sh - installs the Mobile AI Agent inside Termux and puts a
# launcher on the home screen (Termux:Widget) plus a PWA manifest so
# the Web UI can be added to the home screen as a standalone app.
#
# Usage (inside Termux):
#     bash setup.sh
#     ./run.sh          # start it later
# =====================================================================
set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$HOME/.mobile-agent.env"
PORT="${AGENT_PORT:-8900}"
IS_TERMUX=0
case "${PREFIX:-}" in */com.termux/*) IS_TERMUX=1 ;; esac

say(){ printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn(){ printf '\033[1;33m[!]\033[0m %s\n' "$*"; }

say "تثبيت المساعد الذكي للجوال في: $APP_DIR"

# ---------------------------------------------------------------------
# 1) System packages
# ---------------------------------------------------------------------
if [ "$IS_TERMUX" = "1" ]; then
  say "تحديث حزم Termux..."
  pkg update -y || warn "pkg update واجه مشكلة، نكمل."
  say "تثبيت: python git openssh termux-api ..."
  pkg install -y python git openssh termux-api libjpeg-turbo libxml2 libxslt || \
    warn "تعذّر تثبيت بعض الحزم."

  say "محاولة تثبيت Chromium (من tur-repo) للمتصفح الحيّ..."
  pkg install -y tur-repo >/dev/null 2>&1 || true
  pkg install -y chromium >/dev/null 2>&1 || warn "Chromium غير متاح؛ أدوات المتصفح ستُعطّل حتى يُثبّت."
  termux-setup-storage || true
else
  warn "لست داخل Termux — سيُثبَّت للتجربة على سطح المكتب فقط."
  command -v python3 >/dev/null || { warn "ثبّت python3 أولاً"; exit 1; }
fi

PYBIN="$(command -v python || command -v python3)"

# ---------------------------------------------------------------------
# 2) Python dependencies
# ---------------------------------------------------------------------
say "ترقية pip وتثبيت مكتبات بايثون..."
"$PYBIN" -m pip install --upgrade pip wheel >/dev/null 2>&1 || true
"$PYBIN" -m pip install \
  "fastapi>=0.110" "uvicorn[standard]>=0.29" "httpx>=0.27" \
  "apscheduler>=3.10,<4" "SQLAlchemy>=2.0" "pydantic>=2" "playwright>=1.44" \
  || warn "تعذّر تثبيت بعض مكتبات بايثون؛ راجع الأخطاء أعلاه."

# We use the system Chromium (CHROMIUM_BIN); do NOT download Playwright's
# bundled browser (it does not run on Android/ARM).
CHROMIUM_BIN="$(command -v chromium || command -v chromium-browser || true)"

# ---------------------------------------------------------------------
# 3) API keys (free tiers) - created once, edited by the user
# ---------------------------------------------------------------------
if [ ! -f "$ENV_FILE" ]; then
  say "إنشاء ملف المفاتيح: $ENV_FILE (عدّله وضع مفاتيحك المجانية)"
  cat > "$ENV_FILE" <<ENV
# مفاتيح مجانية — املأ واحداً على الأقل.
# Gemini (مجاني):  https://aistudio.google.com/apikey
export GEMINI_API_KEY=""
# OpenRouter (نماذج مجانية): https://openrouter.ai/keys
export OPENROUTER_API_KEY=""
# Groq (مجاني): https://console.groq.com/keys
export GROQ_API_KEY=""
# GitHub token (اختياري، للدفع والسحب): https://github.com/settings/tokens
export GITHUB_TOKEN=""

export AGENT_HOST="127.0.0.1"
export AGENT_PORT="$PORT"
export AGENT_TZ="\$(getprop persist.sys.timezone 2>/dev/null || echo UTC)"
export CHROMIUM_BIN="$CHROMIUM_BIN"
ENV
else
  say "ملف المفاتيح موجود: $ENV_FILE (لم يُغيَّر)"
fi

# ---------------------------------------------------------------------
# 4) PWA manifest + icon (for "Add to home screen")
# ---------------------------------------------------------------------
say "كتابة ملف PWA (manifest + icon)..."
cat > "$APP_DIR/manifest.webmanifest" <<MAN
{
  "name": "Mobile AI Agent",
  "short_name": "AI Agent",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#0b0f17",
  "theme_color": "#0b0f17",
  "icons": [{ "src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any" }]
}
MAN
cat > "$APP_DIR/icon.svg" <<'SVG'
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192"><rect width="192" height="192" rx="40" fill="#0b0f17"/><circle cx="96" cy="78" r="34" fill="none" stroke="#38bdf8" stroke-width="8"/><circle cx="84" cy="74" r="6" fill="#38bdf8"/><circle cx="108" cy="74" r="6" fill="#38bdf8"/><rect x="70" y="120" width="52" height="34" rx="10" fill="#38bdf8"/></svg>
SVG

# ---------------------------------------------------------------------
# 5) run.sh launcher
# ---------------------------------------------------------------------
say "إنشاء run.sh..."
cat > "$APP_DIR/run.sh" <<RUN
#!/data/data/com.termux/files/usr/bin/bash
set -u
cd "$APP_DIR"
[ -f "$ENV_FILE" ] && . "$ENV_FILE"
# keep the device awake while the agent runs
command -v termux-wake-lock >/dev/null && termux-wake-lock || true
URL="http://127.0.0.1:\${AGENT_PORT:-$PORT}"
echo "المساعد يعمل على \$URL"
# open the Web UI once the server is up
( for i in \$(seq 1 20); do
    if curl -s "\$URL/api/status" >/dev/null 2>&1; then
      command -v termux-open-url >/dev/null && termux-open-url "\$URL"; break
    fi; sleep 1; done ) &
exec "$PYBIN" -m uvicorn backend:app --host "\${AGENT_HOST:-127.0.0.1}" --port "\${AGENT_PORT:-$PORT}"
RUN
chmod +x "$APP_DIR/run.sh"

# ---------------------------------------------------------------------
# 6) Termux:Widget shortcut (home-screen launcher)
# ---------------------------------------------------------------------
if [ "$IS_TERMUX" = "1" ]; then
  say "إنشاء اختصار Termux:Widget على الشاشة الرئيسية..."
  mkdir -p "$HOME/.shortcuts" "$HOME/.shortcuts/icons"
  cat > "$HOME/.shortcuts/AI-Agent.sh" <<SC
#!/data/data/com.termux/files/usr/bin/bash
cd "$APP_DIR" && ./run.sh
SC
  chmod +x "$HOME/.shortcuts/AI-Agent.sh"
  cp "$APP_DIR/icon.svg" "$HOME/.shortcuts/icons/AI-Agent.svg" 2>/dev/null || true
fi

# ---------------------------------------------------------------------
# 7) Smoke check
# ---------------------------------------------------------------------
say "فحص سريع للاستيراد..."
( cd "$APP_DIR" && "$PYBIN" -c "import backend; print('backend OK, port', backend.PORT)" ) \
  || warn "فشل الاستيراد — راجع الأخطاء أعلاه."

cat <<DONE

\033[1;32mتم التثبيت!\033[0m
الخطوات:
  1) ضع مفتاحاً مجانياً واحداً على الأقل في:  $ENV_FILE
     (Gemini أو OpenRouter أو Groq — كلها مجانية)
  2) شغّل:   cd "$APP_DIR" && ./run.sh
  3) سيفتح المتصفح على http://127.0.0.1:$PORT
     من قائمة المتصفح اختر "إضافة إلى الشاشة الرئيسية" لأيقونة التطبيق (PWA).
  4) أو أضف ودجت Termux:Widget واختر "AI-Agent" لتشغيله من الشاشة الرئيسية.

للإشعارات والتحكم بالهاتف: ثبّت تطبيق "Termux:API" من F-Droid.
DONE
