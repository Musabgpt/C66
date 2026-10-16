package dev.newal.code.lite.testbridge;
import android.content.Context;
import java.io.File;
import java.nio.charset.StandardCharsets;
/** Drop-in debug bridge for APKs built by MusabAI; captures uncaught crashes without Logcat or ADB. */
public final class MusabTestBridge {
    private MusabTestBridge() {}
    public static void install(Context context) {
        Thread.setDefaultUncaughtExceptionHandler((thread, error) -> {
            try { File f=new File(context.getFilesDir(),"musab_crash.txt"); String text="Thread: "+thread.getName()+"\n"+error+"\n"+android.util.Log.getStackTraceString(error); java.nio.file.Files.write(f.toPath(),text.getBytes(StandardCharsets.UTF_8)); } catch (Exception ignored) {}
            android.os.Process.killProcess(android.os.Process.myPid());
        });
    }
    public static void recordTest(Context context, String name, String result) { write(context, "musab_test.txt", name + "\n" + result); }
    public static void recordAnr(Context context, String activity, String detail) { write(context, "musab_anr.txt", "Activity: " + activity + "\n" + detail); }
    private static void write(Context context, String file, String text) { try { java.nio.file.Files.write(new File(context.getFilesDir(), file).toPath(), text.getBytes(StandardCharsets.UTF_8)); } catch (Exception ignored) {} }
}
