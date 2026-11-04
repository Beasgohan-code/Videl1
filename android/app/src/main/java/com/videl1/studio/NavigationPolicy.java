package com.videl1.studio;

import java.net.URI;
import java.net.URLDecoder;
import java.util.Arrays;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;

/** Classification only. External destinations are never loaded in the WebView. */
final class NavigationPolicy {
    static final String HOST = "videl1-frontend.vercel.app";
    static final String START_URL = "https://" + HOST + "/app";
    private static final Set<String> SECRET_KEYS = new HashSet<>(Arrays.asList("token", "api_key", "session", "session_token", "bot_token", "secret", "admin_token", "password", "key"));
    enum Decision { INTERNAL, EXTERNAL, BLOCK }

    static Decision classify(String value) {
        try {
            URI uri = new URI(value);
            String scheme = uri.getScheme();
            if (scheme == null || uri.getRawUserInfo() != null) return Decision.BLOCK;
            scheme = scheme.toLowerCase(Locale.ROOT);
            boolean isHttps = "https".equals(scheme) && uri.getHost() != null && (uri.getPort() == -1 || uri.getPort() == 443);
            if (isHttps && HOST.equalsIgnoreCase(uri.getHost())) return Decision.INTERNAL;
            if ("mailto".equals(scheme)) return uri.isOpaque() && !value.contains("?") && uri.getRawFragment() == null ? Decision.EXTERNAL : Decision.BLOCK;
            if (!isHttps && !("tg".equals(scheme) && uri.getHost() != null)) return Decision.BLOCK;
            String fragment = uri.getFragment();
            if (fragment != null && fragment.startsWith("l.")) return Decision.BLOCK;
            String query = uri.getRawQuery();
            if (query != null) {
                for (String part : query.split("&")) {
                    String key = part.split("=", 2)[0];
                    if (SECRET_KEYS.contains(URLDecoder.decode(key, "UTF-8").toLowerCase(Locale.ROOT))) return Decision.BLOCK;
                }
            }
            return Decision.EXTERNAL;
        } catch (Exception ignored) {
            return Decision.BLOCK;
        }
    }
    private NavigationPolicy() { }
}
