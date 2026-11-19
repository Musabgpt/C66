package dev.newal.code.lite;

import android.Manifest;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.view.MotionEvent;
import android.webkit.WebView;
import android.widget.Button;
import android.widget.Toast;
import java.util.ArrayList;
import java.util.Locale;
import java.util.UUID;
import org.json.JSONObject;
import org.json.JSONTokener;

/** A foreground-only sequence of spoken questions about the page and spoken replies in the current chat. */
final class BrowserConversation implements RecognitionListener {
    private final MainActivity activity;
    private final WebView page;
    private final Button toggle;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final boolean ar = "ar".equals(Locale.getDefault().getLanguage());
    private SpeechRecognizer mic;
    private TextToSpeech speaker;
    private boolean active;
    private String turn = "";
    private float pointX = -1, pointY = -1;
    private String pointUrl = "";
    private final Runnable listenAgain = this::listen;
    private final Runnable expired = () -> fail("No reply", "تأخر الرد");

    BrowserConversation(MainActivity activity, WebView page, Button toggle) {
        this.activity = activity;
        this.page = page;
        this.toggle = toggle;
        label();
        toggle.setOnClickListener(v -> { if (active) stop(); else start(); });
    }

    private void label() {
        toggle.setText(active ? (ar ? "إيقاف الصوت" : "Stop voice") : (ar ? "تحدث عن الصفحة" : "Talk about page"));
    }

    private void state(String en, String arabic) {
        if (active) toggle.setText(ar ? arabic + " · إيقاف" : en + " · Stop");
        android.util.Log.i("MusabPageVoice", en);
    }

    private void fail(String en, String arabic) {
        android.util.Log.i("MusabPageVoice", en);
        Toast.makeText(activity, ar ? arabic : en, Toast.LENGTH_LONG).show();
        stop();
    }

    private void start() {
        if (activity.checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            activity.requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, 682);
            return;
        }
        if (!SpeechRecognizer.isRecognitionAvailable(activity)) {
            fail("Speech recognition unavailable", "التعرف على الصوت غير متاح");
            return;
        }
        active = true;
        label();
        pointX = -1;
        final float[] down = new float[2];
        final int slop = android.view.ViewConfiguration.get(activity).getScaledTouchSlop();
        page.setOnTouchListener((v,e) -> {
            if (e.getActionMasked() == MotionEvent.ACTION_DOWN) {
                down[0] = e.getX(); down[1] = e.getY();
            }
            if (e.getActionMasked() == MotionEvent.ACTION_MOVE &&
                    Math.hypot(e.getX() - down[0], e.getY() - down[1]) > slop) pointX = -1;
            if (e.getActionMasked() == MotionEvent.ACTION_UP &&
                    Math.hypot(e.getX() - down[0], e.getY() - down[1]) <= slop) {
                pointX = e.getX() / Math.max(1, page.getWidth());
                pointY = e.getY() / Math.max(1, page.getHeight());
                pointUrl = page.getUrl();
                MotionEvent cancel = MotionEvent.obtain(e);
                cancel.setAction(MotionEvent.ACTION_CANCEL);
                page.onTouchEvent(cancel);
                cancel.recycle();
                return true;
            }
            return false;
        });
        mic = SpeechRecognizer.createSpeechRecognizer(activity);
        mic.setRecognitionListener(this);
        final String init = UUID.randomUUID().toString();
        turn = init;
        speaker = new TextToSpeech(activity, status -> main.post(() -> {
            if (!active || !turn.equals(init)) return;
            if (status != TextToSpeech.SUCCESS || speaker.setLanguage(Locale.getDefault()) < 0) {
                fail("Voice output unavailable for this language", "صوت القراءة غير متاح لهذه اللغة");
                return;
            }
            speaker.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                public void onStart(String id) { main.post(() -> {
                    if (id.equals(turn)) state("Speaking", "يقرأ الرد");
                }); }
                public void onError(String id) { main.post(() -> { if (id.equals(turn)) fail("Speech failed", "تعذرت قراءة الرد"); }); }
                public void onDone(String id) { main.post(() -> {
                    if (active && id.equals(turn)) {
                        state("Reply finished", "انتهت القراءة");
                        turn = ""; main.postDelayed(listenAgain, 500);
                    }
                }); }
            });
            turn = "";
            listen();
        }));
    }

    private void listen() {
        if (!active || !turn.isEmpty()) return;
        Intent i = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
        i.putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag());
        try { mic.startListening(i); }
        catch (RuntimeException ex) { fail("Microphone unavailable", "تعذر بدء الميكروفون"); }
    }

    public void onResults(Bundle b) {
        if (!active || !turn.isEmpty()) return;
        ArrayList<String> words = b == null ? null : b.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
        if (words == null || words.isEmpty() || words.get(0).trim().isEmpty()) { main.postDelayed(listenAgain, 500); return; }
        String id = UUID.randomUUID().toString();
        state("Waiting for answer", "ينتظر الإجابة");
        turn = id;
        String url = page.getUrl();
        if (url == null || !(url.startsWith("https://") || url.startsWith("http://"))) { stop(); return; }
        float x = url.equals(pointUrl) ? pointX : -1;
        String js = "(function(){var e=document.elementFromPoint(innerWidth*" + x + ",innerHeight*" + pointY
                + ");var sel=String(getSelection()).slice(0,600);var hit=e&&!e.closest('input,textarea,form,[contenteditable=true]')?"
                + "(e.getAttribute('aria-label')||e.alt||e.innerText||'').trim().slice(0,600):'';"
                + "return JSON.stringify({title:document.title.slice(0,160),selected:sel||hit});})()";
        main.postDelayed(expired, 120000);
        page.evaluateJavascript(js, raw -> {
            if (!active || !turn.equals(id)) return;
            try {
                if (!url.equals(page.getUrl())) { turn=""; main.removeCallbacks(expired); listen(); return; }
                JSONObject context = new JSONObject((String)new JSONTokener(raw).nextValue());
                context.put("url", url);
                if (x >= 0) {
                    int[] origin = new int[2];
                    page.getLocationOnScreen(origin);
                    context.put("point_on_screen_px", new org.json.JSONArray()
                            .put(origin[0] + Math.round(x * page.getWidth()))
                            .put(origin[1] + Math.round(pointY * page.getHeight())));
                }
                activity.browserVoiceQuestion(id, words.get(0), context);
            } catch (Exception ex) { fail("Page unavailable", "تعذرت قراءة الصفحة"); }
        });
    }

    void answer(String id, String text) {
        if (!active || !id.equals(turn)) return;
        main.removeCallbacks(expired);
        if (text == null || text.trim().isEmpty()) { fail("No spoken reply", "لم يصل رد صوتي"); return; }
        if (speaker.speak(text.substring(0, Math.min(text.length(), TextToSpeech.getMaxSpeechInputLength())),
                TextToSpeech.QUEUE_FLUSH, null, id) == TextToSpeech.ERROR) fail("Speech failed", "تعذرت قراءة الرد");
    }

    void stop() {
        active = false;
        turn = "";
        main.removeCallbacksAndMessages(null);
        page.setOnTouchListener(null);
        if (mic != null) { mic.destroy(); mic = null; }
        if (speaker != null) { speaker.stop(); speaker.shutdown(); speaker = null; }
        label();
    }

    public void onError(int code) {
        if (!active || !turn.isEmpty()) return;
        if (code == SpeechRecognizer.ERROR_SPEECH_TIMEOUT || code == SpeechRecognizer.ERROR_NO_MATCH)
            main.postDelayed(listenAgain, 650);
        else fail("Speech recognition stopped (" + code + ")", "توقف التعرف على الصوت (" + code + ")");
    }
    public void onReadyForSpeech(Bundle b) {
        if (active && turn.isEmpty()) state("Listening", "يستمع");
    }
    public void onBeginningOfSpeech() {}
    public void onEndOfSpeech() {}
    public void onPartialResults(Bundle b) {}
    public void onBufferReceived(byte[] b) {}
    public void onRmsChanged(float rms) {}
    public void onEvent(int event, Bundle b) {}
}
