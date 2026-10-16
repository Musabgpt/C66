package dev.newal.code.lite;

import org.junit.Test;
import static org.junit.Assert.*;

public class ActivepiecesMcpTest {
    @Test public void oauthChallengeAcceptsSameOriginResourceMetadata() throws Exception {
        String header = "Bearer realm=\"mcp\", resource_metadata=\"https://automation.example/.well-known/oauth-protected-resource/mcp\"";
        assertEquals(
                "https://automation.example/.well-known/oauth-protected-resource/mcp",
                ActivepiecesMcp.resourceMetadataFromChallenge(header, "https://automation.example/mcp")
        );
    }

    @Test public void oauthChallengeRejectsCrossOriginMetadata() {
        String header = "Bearer resource_metadata=\"https://evil.example/.well-known/oauth-protected-resource/mcp\"";
        assertThrows(SecurityException.class, () ->
                ActivepiecesMcp.resourceMetadataFromChallenge(header, "https://automation.example/mcp"));
    }

    @Test public void oauthChallengeCanBeAbsent() throws Exception {
        assertNull(ActivepiecesMcp.resourceMetadataFromChallenge(
                "Bearer realm=\"mcp\"", "https://automation.example/mcp"));
        assertNull(ActivepiecesMcp.resourceMetadataFromChallenge(
                null, "https://automation.example/mcp"));
    }
}
