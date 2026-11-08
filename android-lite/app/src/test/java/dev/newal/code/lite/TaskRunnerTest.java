package dev.newal.code.lite;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertTrue;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

import org.json.JSONObject;
import org.junit.BeforeClass;
import org.junit.Test;

/**
 * The runner carries out the shipped Facebook post procedure on a simulated Facebook: in English, in Arabic, in the
 * version with a Next screen; it stops where the screen differs and says what is missing.
 */
public class TaskRunnerTest {
    private static TaskMemory.Proc post;
    private static TaskMemory.Proc guidance;

    @BeforeClass
    public static void load() throws Exception {
        JdbcTaskMemoryStore store = JdbcTaskMemoryStore.shipped();
        post = store.get("cur.facebook.post");
        guidance = store.get("insta.1") != null ? store.get("insta.1") : store.get("#16340");
    }

    /** A Facebook that walks feed -> create post -> (next) -> posted, with the labels of one interface language. */
    static final class FakeFacebook implements TaskRunner.Device {
        final Set<String> installed = new HashSet<>(Arrays.asList("com.facebook.katana"));
        final List<String> did = new ArrayList<>();
        String composer = "What's on your mind?";
        String post = "POST";
        boolean nextScreen;
        boolean audienceDialog;
        boolean control = true;
        String front = "com.android.launcher";
        String state = "launcher";
        String typed = "";

        String screen() {
            switch (state) {
                case "feed":
                    return "App: Facebook (com.facebook.katana)\n[1] Home · tap\n[2] " + composer + " · tap";
                case "create":
                    return "App: Facebook (com.facebook.katana)\n[1] Create post\n[2] " + (typed.isEmpty() ? "hint: "
                            + composer : typed) + " · field, focused\n[3] " + (nextScreen ? "NEXT" : post) + " · button, tap";
                case "share":
                    return "App: Facebook (com.facebook.katana)\n[1] Post settings\n[2] " + post + " · button, tap";
                case "audience":
                    return "App: Facebook (com.facebook.katana)\n[1] Who can see your post?\n[2] Public · tap\n[3] Done · tap";
                case "posted":
                    return "App: Facebook (com.facebook.katana)\n[1] Home · tap\n[2] " + composer + " · tap\n[3] You\n[4] "
                            + typed;
                default:
                    return "App: Launcher (com.android.launcher)\n[1] Facebook · tap";
            }
        }

        boolean on(String label) {
            String f = TextFold.fold(label);
            for (String line : screen().split("\n")) {
                if (line.startsWith("[") && TextFold.fold(line).contains(f)) {
                    return true;
                }
            }
            return false;
        }

        @Override
        public JSONObject act(JSONObject a) throws Exception {
            String action = a.getString("action");
            did.add(action + (a.has("text") ? " " + a.getString("text") : "") + (a.has("name") ? " " + a.getString("name") : ""));
            switch (action) {
                case "screen":
                    return new JSONObject().put("ok", true).put("text", screen());
                case "tap": {
                    String t = a.getString("text");
                    if (!on(t)) {
                        throw new IllegalArgumentException("nothing on the screen says \"" + t + "\"");
                    }
                    String f = TextFold.fold(t);
                    if (state.equals("feed") && f.equals(TextFold.fold(composer))) {
                        state = "create";
                    } else if (state.equals("create") && nextScreen && f.equals("next")) {
                        state = "share";
                    } else if ((state.equals("create") || state.equals("share")) && f.equals(TextFold.fold(post))) {
                        state = audienceDialog ? "audience" : "posted";
                    }
                    return new JSONObject().put("ok", true).put("text", "tapped " + t);
                }
                case "type":
                    if (!state.equals("create")) {
                        throw new IllegalArgumentException("no text field to type into: tap one first");
                    }
                    typed = a.getString("text");
                    return new JSONObject().put("ok", true).put("text", "typed");
                case "open_app":
                    throw new IllegalArgumentException("no app called \"" + a.optString("name") + "\"");
                default:
                    return new JSONObject().put("ok", true).put("text", "done");
            }
        }

        @Override
        public boolean launch(String pkg) {
            if (!installed.contains(pkg)) {
                return false;
            }
            front = pkg;
            state = "feed";
            return true;
        }

        @Override
        public String foreground() {
            return control ? front : "";
        }

        @Override
        public boolean screenControl() {
            return control;
        }

        @Override
        public boolean longPress(String text) {
            return on(text);
        }

        @Override
        public void sleep(long ms) {
        }
    }

    @Test
    public void postsOnFacebookInEnglish() {
        FakeFacebook fb = new FakeFacebook();
        TaskRunner.Result r = TaskRunner.run(fb, post, Arrays.asList("صباح الخير"), null, null);
        assertTrue(r.message, r.done);
        assertEquals("posted", fb.state);
        assertEquals("صباح الخير", fb.typed);
        assertTrue(r.message, r.message.contains("checked: \"صباح الخير\" is on the screen"));
        assertTrue(fb.did.contains("tap POST"));
    }

    @Test
    public void postsOnFacebookInArabic() {
        FakeFacebook fb = new FakeFacebook();
        fb.composer = "بمَ تفكر؟";
        fb.post = "نشر";
        TaskRunner.Result r = TaskRunner.run(fb, post, Arrays.asList("مرحبا يا جماعة"), null, null);
        assertTrue(r.message, r.done);
        assertEquals("posted", fb.state);
        assertTrue(fb.did.contains("tap نشر"));
    }

    @Test
    public void theVersionWithANextScreen() {
        FakeFacebook fb = new FakeFacebook();
        fb.nextScreen = true;
        fb.post = "Share now";
        TaskRunner.Result r = TaskRunner.run(fb, post, Arrays.asList("hello"), null, null);
        assertTrue(r.message, r.done);
        assertTrue(fb.did.contains("tap Next"));
        assertTrue(fb.did.contains("tap Share now"));
    }

    @Test
    public void stopsWhereTheScreenDiffers() {
        FakeFacebook fb = new FakeFacebook();
        fb.audienceDialog = true;              // a dialog Facebook shows the first time: the check finds no post
        TaskRunner.Result r = TaskRunner.run(fb, post, Arrays.asList("hello"), null, null);
        assertFalse(r.message, r.done);
        assertTrue(r.message, r.message.contains("not on the screen now"));
        assertTrue(r.screen, r.screen.contains("Who can see your post?"));

        FakeFacebook other = new FakeFacebook();
        other.composer = "Write a post";       // a version the procedure does not know: stops at the composer
        r = TaskRunner.run(other, post, Arrays.asList("hello"), null, null);
        assertFalse(r.done);
        assertEquals(1, r.stoppedAt);
        assertTrue(r.message, r.message.startsWith("Stopped at step 2 of"));
        assertTrue(r.screen, r.screen.contains("Write a post"));
    }

    @Test
    public void saysWhatIsMissing() {
        FakeFacebook fb = new FakeFacebook();
        TaskRunner.Result r = TaskRunner.run(fb, post, new ArrayList<>(), null, null);
        assertFalse(r.done);
        assertTrue(r.message, r.message.contains("needs values for [1]"));
        assertTrue(fb.did.isEmpty());

        fb.installed.clear();                  // Facebook not installed: open_app by name fails, the run stops there
        r = TaskRunner.run(fb, post, Arrays.asList("x"), null, null);
        assertEquals(0, r.stoppedAt);
        assertTrue(r.message, r.message.contains("no app called"));

        FakeFacebook off = new FakeFacebook();
        off.control = false;
        r = TaskRunner.run(off, post, Arrays.asList("x"), null, null);
        assertTrue(r.message, r.message.startsWith("ACCESSIBILITY_OFF"));

        r = TaskRunner.run(new FakeFacebook(), guidance, new ArrayList<>(), null, null);
        assertFalse(r.done);
        assertTrue(r.message, r.message.contains("guidance"));
    }
}
