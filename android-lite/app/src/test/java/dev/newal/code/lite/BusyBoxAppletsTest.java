package dev.newal.code.lite;

import org.junit.Test;
import org.junit.Rule;
import org.junit.rules.TemporaryFolder;

import java.io.File;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Arrays;

import static org.junit.Assert.*;

public class BusyBoxAppletsTest {
    @Rule public TemporaryFolder temp = new TemporaryFolder();

    @Test public void automaticallyInstallsAppletsWithoutReplacingSystemShell() throws Exception {
        File libDir = temp.newFolder("native");
        File bin = temp.newFolder("bin");
        File busybox = new File(libDir, "libbusybox.so");
        Files.write(busybox.toPath(), new byte[] {1, 2, 3});

        BusyBoxApplets.install(bin, busybox);

        assertTrue(BusyBoxApplets.ready(bin, busybox));
        for (String name : Arrays.asList("busybox", "grep", "find", "awk", "sed", "ls",
                                        "mkdir", "rm", "cp", "mv", "xargs")) {
            Path link = new File(bin, name).toPath();
            assertTrue("missing automatic " + name, Files.isSymbolicLink(link));
            assertEquals(busybox.getAbsolutePath(), Files.readSymbolicLink(link).toString());
        }
        for (String existingTool : Arrays.asList("sh", "bash", "python", "python3", "git", "node", "npm")) {
            assertFalse("must not replace " + existingTool,
                        Files.exists(new File(bin, existingTool).toPath()));
        }
        BusyBoxApplets.install(bin, busybox); // Repeat on warm launch, no exception.
        assertTrue(BusyBoxApplets.ready(bin, busybox));
    }

    @Test public void missingBinaryDoesNotInstallBrokenAliases() throws Exception {
        File bin = temp.newFolder("bin");
        File absent = new File(temp.getRoot(), "does-not-exist.so");
        BusyBoxApplets.install(bin, absent);
        assertFalse(BusyBoxApplets.ready(bin, absent));
        assertEquals(0, bin.list().length);
    }

    @Test public void existingRealFilesAreNeverOverwritten() throws Exception {
        File bin = temp.newFolder("bin");
        File libDir = temp.newFolder("native");
        File executable = new File(libDir, "libbusybox.so");
        Files.write(executable.toPath(), new byte[] {1, 2, 3});
        File custom = new File(bin, "grep");
        Files.write(custom.toPath(), "keep user binary".getBytes("UTF-8"));

        BusyBoxApplets.install(bin, executable);
        assertFalse(Files.isSymbolicLink(custom.toPath()));
        assertEquals("keep user binary",
                     new String(Files.readAllBytes(custom.toPath()), "UTF-8"));
        assertTrue(Files.isSymbolicLink(new File(bin, "find").toPath()));
    }

    @Test public void staleBusyBoxAliasesAreCorrectedOnUpdate() throws Exception {
        File bin = temp.newFolder("bin");
        File libDir = temp.newFolder("native");
        File target = new File(libDir, "libbusybox.so");
        Files.write(target.toPath(), new byte[] {1, 2, 3});
        Files.createSymbolicLink(new File(bin, "grep").toPath(),
                                 new File(libDir, "removed-old-busybox.so").toPath());
        BusyBoxApplets.install(bin, target);
        assertEquals(target.getAbsolutePath(),
                     Files.readSymbolicLink(new File(bin, "grep").toPath()).toString());
    }
}
