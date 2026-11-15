package com.videl1.studio;

import org.junit.Test;
import static org.junit.Assert.assertEquals;
import static com.videl1.studio.NavigationPolicy.Decision.*;

public class NavigationPolicyTest {
    @Test public void keepsOnlyExactHttpsFrontendInsideWebView() {
        assertEquals(INTERNAL, NavigationPolicy.classify("https://videl1-frontend.vercel.app/app"));
        assertEquals(INTERNAL, NavigationPolicy.classify("https://VIDEL1-FRONTEND.VERCEL.APP:443/app#l.example"));
        assertEquals(EXTERNAL, NavigationPolicy.classify("https://example.org/docs"));
        assertEquals(BLOCK, NavigationPolicy.classify("http://videl1-frontend.vercel.app/app"));
        assertEquals(EXTERNAL, NavigationPolicy.classify("https://videl1-frontend.vercel.app.evil.test/app"));
        assertEquals(BLOCK, NavigationPolicy.classify("https://videl1-frontend.vercel.app:444/app"));
        assertEquals(BLOCK, NavigationPolicy.classify("https://user@videl1-frontend.vercel.app/app"));
    }
    @Test public void blocksDangerousSchemesAndSensitiveExternalLinks() {
        assertEquals(BLOCK, NavigationPolicy.classify("javascript:alert(1)"));
        assertEquals(BLOCK, NavigationPolicy.classify("file:///etc/passwd"));
        assertEquals(BLOCK, NavigationPolicy.classify("intent://example#Intent;end"));
        assertEquals(BLOCK, NavigationPolicy.classify("https://example.org/?%74oken=value"));
        assertEquals(BLOCK, NavigationPolicy.classify("https://example.org/app#l.example"));
        assertEquals(BLOCK, NavigationPolicy.classify("mailto:test@example.org?token=value"));
        assertEquals(BLOCK, NavigationPolicy.classify("tg://resolve?session=value"));
        assertEquals(EXTERNAL, NavigationPolicy.classify("tg://resolve?domain=example"));
        assertEquals(EXTERNAL, NavigationPolicy.classify("mailto:test@example.org"));
        assertEquals(BLOCK, NavigationPolicy.classify("not a url"));
    }
}
