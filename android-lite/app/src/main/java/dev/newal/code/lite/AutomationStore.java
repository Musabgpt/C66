package dev.newal.code.lite;

import android.content.Context;
import java.io.File;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import org.json.JSONArray;
import org.json.JSONObject;

/** Explicitly recorded phone workflows. Replay is requested by the agent and still uses normal permissions. */
final class AutomationStore {
    private static boolean recording, replaying; private static String name; private static final JSONArray actions = new JSONArray();
    static synchronized void record(String action, JSONObject args) {
        if (recording && !replaying && !action.startsWith("automation_")) try { actions.put(new JSONObject(args.toString())); } catch (Exception ignored) { }
    }
    static synchronized JSONObject start(String n) throws Exception { if (recording) throw new IllegalStateException("a workflow is already recording"); recording=true; name=n.trim().isEmpty()?"workflow":n.trim(); while(actions.length()>0) actions.remove(0); return Phone.ok("recording workflow " + name); }
    static synchronized JSONObject stop(Context c) throws Exception { if (!recording) throw new IllegalStateException("no workflow is recording"); recording=false; File d=new File(c.getFilesDir(),"workflows"); d.mkdirs(); String safe=name.replaceAll("[^A-Za-z0-9_-]", "_"); java.nio.file.Files.write(new File(d,safe+".json").toPath(), actions.toString().getBytes(StandardCharsets.UTF_8)); JSONObject o=Phone.ok("recorded " + actions.length() + " actions"); o.put("name",safe); o.put("actions",actions.length()); return o; }
    static synchronized JSONObject list(Context c) throws Exception { File d=new File(c.getFilesDir(),"workflows"); JSONArray out=new JSONArray(); if(d.isDirectory()) for(File f:d.listFiles()) if(f.getName().endsWith(".json")) out.put(f.getName().substring(0,f.getName().length()-5)); return new JSONObject().put("ok",true).put("workflows",out); }
    static synchronized JSONObject replay(Context c,String n) throws Exception { if(recording) throw new IllegalStateException("stop recording first"); String safe=n.trim().replaceAll("[^A-Za-z0-9_-]", "_"); JSONArray run; File f=new File(new File(c.getFilesDir(),"workflows"),safe+".json"); if(f.isFile()) run=new JSONArray(new String(java.nio.file.Files.readAllBytes(f.toPath()),StandardCharsets.UTF_8)); else if(safe.equals(name)&&actions.length()>0) run=actions; else throw new IllegalArgumentException("workflow not found; record it first"); replaying=true; try { for(int i=0;i<run.length();i++) Phone.handle(c,run.getJSONObject(i)); } finally { replaying=false; } return Phone.ok("replayed " + run.length() + " actions"); }
}
