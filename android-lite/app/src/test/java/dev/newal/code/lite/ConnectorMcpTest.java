package dev.newal.code.lite;

import org.json.JSONObject;
import org.junit.Test;
import static org.junit.Assert.*;
import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;

public class ConnectorMcpTest {
    private static InputStream bytes(String value) {
        return new ByteArrayInputStream(value.getBytes(StandardCharsets.UTF_8));
    }

    @Test public void jsonResponseMustMatchRequest() throws Exception {
        assertTrue(ConnectorMcp.decode(bytes("{\"jsonrpc\":\"2.0\",\"id\":7,\"result\":{\"tools\":[]}}"), "application/json", 7).has("result"));
        assertThrows(IllegalStateException.class, () -> ConnectorMcp.decode(bytes("{\"id\":8,\"result\":{}}"), "application/json", 7));
    }

    @Test public void sseStopsBeforeReadingPastMatchingReply() throws Exception {
        byte[] payload = (": heartbeat\r\n\r\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/message\"}\n\n" +
                "event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":9,\n" +
                "data: \"result\":{\"text\":\"مرحبا\"}}\n\n").getBytes(StandardCharsets.UTF_8);
        InputStream stream = new InputStream() {
            int position;
            public int read() throws IOException {
                if (position >= payload.length) throw new IOException("Reading here would hang on an open SSE stream");
                return payload[position++] & 255;
            }
        };
        JSONObject result = ConnectorMcp.decode(stream, "text/event-stream; charset=utf-8", 9);
        assertEquals("مرحبا", result.getJSONObject("result").getString("text"));
    }

    @Test public void missingReplyAndOversizedStreamFail() {
        assertThrows(IllegalStateException.class, () -> ConnectorMcp.decode(bytes("data: {\"id\":1,\"result\":{}}\n\n"), "text/event-stream", 2));
        InputStream endless = new InputStream() { public int read() { return 'x'; } };
        assertThrows(IllegalStateException.class, () -> ConnectorMcp.decode(endless, "text/event-stream", 2));
    }

    @Test public void endpointsCannotBeSelectedByTheModel() {
        assertEquals("https://mcp.notion.com/mcp", ConnectorMcp.endpoint("notionmcp"));
        assertThrows(IllegalArgumentException.class, () -> ConnectorMcp.endpoint("https://other.example"));
        assertFalse(ConnectorMcp.supports("github"));
    }
}
