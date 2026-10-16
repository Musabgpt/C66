package dev.newal.code.lite;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.pdf.PdfDocument;
import android.text.Layout;
import android.text.StaticLayout;
import android.text.TextPaint;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;

/** Android's text layout keeps Unicode/Arabic shaping in generated PDF pages. */
final class DocumentFiles {
    static JSONObject pdf(Context ctx, JSONObject args) throws Exception {
        File dest = new File(args.getString("path")).getCanonicalFile();
        String own = ctx.getFilesDir().getCanonicalPath() + File.separator;
        String shared = android.os.Environment.getExternalStorageDirectory().getCanonicalPath() + File.separator;
        boolean storage = android.os.Build.VERSION.SDK_INT >= 30
                ? android.os.Environment.isExternalStorageManager()
                : ctx.checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE) == android.content.pm.PackageManager.PERMISSION_GRANTED;
        if (!dest.getPath().startsWith(own) && !(storage && dest.getPath().startsWith(shared)))
            throw new SecurityException("PDF destination is outside allowed storage");
        if (!dest.getName().toLowerCase(java.util.Locale.ROOT).endsWith(".pdf"))
            throw new IllegalArgumentException("PDF destination must end with .pdf");
        String content = args.optString("content"), title = args.optString("title");
        if (content.length() > 200000 || title.length() > 1000)
            throw new IllegalArgumentException("PDF text is too long; split it into smaller documents");
        String text = (title.isEmpty() ? "" : title + "\n\n") + content;
        if (text.isEmpty()) text = " ";
        TextPaint paint = new TextPaint(android.graphics.Paint.ANTI_ALIAS_FLAG);
        paint.setColor(Color.BLACK); paint.setTextSize(14);
        StaticLayout layout = StaticLayout.Builder.obtain(text, 0, text.length(), paint, 499)
                .setAlignment(Layout.Alignment.ALIGN_NORMAL).setIncludePad(false)
                .setLineSpacing(4, 1).build();
        dest.getParentFile().mkdirs();
        File temp = File.createTempFile("newal-pdf-", ".tmp", dest.getParentFile());
        int pages = 0, line = 0;
        PdfDocument pdf = new PdfDocument();
        try {
            while (line < layout.getLineCount()) {
                if (++pages > 200) throw new IllegalArgumentException("PDF exceeds 200 pages");
                int top = layout.getLineTop(line), next = line;
                while (next < layout.getLineCount() && layout.getLineBottom(next) - top <= 730) next++;
                if (next == line) next++;
                PdfDocument.Page page = pdf.startPage(new PdfDocument.PageInfo.Builder(595, 842, pages).create());
                Canvas canvas = page.getCanvas();
                canvas.save(); canvas.translate(48, 48);
                canvas.clipRect(0, 0, 499, layout.getLineTop(next) - top);
                canvas.translate(0, -top); layout.draw(canvas); canvas.restore();
                canvas.drawText(String.valueOf(pages), 290, 815, paint);
                pdf.finishPage(page); line = next;
            }
            try (FileOutputStream out = new FileOutputStream(temp)) { pdf.writeTo(out); }
            Files.move(temp.toPath(), dest.toPath(), StandardCopyOption.REPLACE_EXISTING);
        } finally { pdf.close(); temp.delete(); }
        return new JSONObject().put("ok", true).put("pages", pages).put("path", dest.getPath());
    }
}
