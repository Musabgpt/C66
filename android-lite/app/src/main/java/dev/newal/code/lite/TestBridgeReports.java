package dev.newal.code.lite;
import android.content.Context;
import java.io.File;
import java.nio.charset.StandardCharsets;
/** Reads reports exported by an instrumented APK's optional MusabTestBridge. */
final class TestBridgeReports {
    static String read(Context c) throws Exception { StringBuilder out=new StringBuilder(); for(String n:new String[]{"musab_crash.txt","musab_anr.txt","musab_test.txt"}) { File f=new File(c.getFilesDir(),n); if(f.isFile()) out.append("== ").append(n).append(" ==\n").append(new String(java.nio.file.Files.readAllBytes(f.toPath()),StandardCharsets.UTF_8)).append('\n'); } return out.length()==0?"(no local test reports)":out.toString(); }
}
