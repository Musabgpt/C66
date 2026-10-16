package dev.newal.code.lite;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.webkit.MimeTypeMap;
import java.io.File;
import java.io.FileNotFoundException;

/** Read-only content bridge; Android rejects file:// URIs sent outside the app. */
public final class SafeFileProvider extends ContentProvider {
    private File root;
    @Override public boolean onCreate() { root = getContext().getFilesDir().getParentFile(); return true; }
    private File resolve(Uri uri) throws FileNotFoundException {
        String raw = uri.getEncodedPath(); if (raw == null) throw new FileNotFoundException("missing path");
        File f = new File(root, Uri.decode(raw.startsWith("/") ? raw.substring(1) : raw));
        try { File c=f.getCanonicalFile(), r=root.getCanonicalFile(); if (!c.getPath().startsWith(r.getPath()+File.separator)||!c.isFile()) throw new FileNotFoundException("file not allowed"); return c; }
        catch (java.io.IOException e) { throw new FileNotFoundException("invalid path"); }
    }
    @Override public String getType(Uri uri) { String e=MimeTypeMap.getFileExtensionFromUrl(uri.toString()).toLowerCase(java.util.Locale.ROOT); String t=MimeTypeMap.getSingleton().getMimeTypeFromExtension(e); return t==null?"application/octet-stream":t; }
    @Override public ParcelFileDescriptor openFile(Uri uri,String mode)throws FileNotFoundException { if(!"r".equals(mode))throw new FileNotFoundException("read only"); return ParcelFileDescriptor.open(resolve(uri),ParcelFileDescriptor.MODE_READ_ONLY); }
    @Override public Cursor query(Uri uri,String[] p,String s,String[] a,String sort){try{File f=resolve(uri);MatrixCursor c=new MatrixCursor(new String[]{"_display_name","_size"});c.addRow(new Object[]{f.getName(),f.length()});return c;}catch(Exception e){return null;}}
    @Override public int delete(Uri u,String s,String[] a){return 0;} @Override public int update(Uri u,ContentValues v,String s,String[] a){return 0;} @Override public Uri insert(Uri u,ContentValues v){return null;}
}
