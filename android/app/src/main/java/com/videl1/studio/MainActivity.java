package com.videl1.studio;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.webkit.CookieManager;
import android.webkit.GeolocationPermissions;
import android.webkit.PermissionRequest;
import android.webkit.SslErrorHandler;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.TextView;
import android.widget.Toast;

import java.util.ArrayList;

public class MainActivity extends Activity {
    private static final int FILE_PICKER = 1001;
    private WebView webView;
    private LinearLayout offline;
    private ValueCallback<Uri[]> fileCallback;
    private boolean mainFrameFailed;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        FrameLayout root = new FrameLayout(this);
        root.setBackgroundColor(Color.rgb(11, 14, 19));
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((view, insets) -> {
                android.graphics.Insets safe = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout() | WindowInsets.Type.ime());
                view.setPadding(safe.left, safe.top, safe.right, safe.bottom);
                return insets;
            });
        }
        webView = new WebView(this);
        root.addView(webView, new FrameLayout.LayoutParams(-1, -1));
        offline = createOfflinePanel();
        offline.setVisibility(View.GONE);
        root.addView(offline, new FrameLayout.LayoutParams(-1, -1));
        setContentView(root);

        WebView.setWebContentsDebuggingEnabled(false);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true); // Required by the existing Studio.
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(false);
        settings.setAllowContentAccess(true); // User-selected documents from the system picker.
        settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        settings.setJavaScriptCanOpenWindowsAutomatically(false);
        settings.setSupportMultipleWindows(false);
        if (Build.VERSION.SDK_INT >= 26) settings.setSafeBrowsingEnabled(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, false);
        webView.setWebViewClient(new StudioClient());
        webView.setWebChromeClient(new StudioChrome());
        webView.setDownloadListener((url, userAgent, disposition, mimeType, length) ->
                Toast.makeText(this, R.string.download_unavailable, Toast.LENGTH_LONG).show());
        webView.loadUrl(NavigationPolicy.START_URL);
    }

    private LinearLayout createOfflinePanel() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL);
        panel.setGravity(Gravity.CENTER);
        int pad = (int) (28 * getResources().getDisplayMetrics().density);
        panel.setPadding(pad, pad, pad, pad);
        panel.setBackgroundColor(Color.rgb(11, 14, 19));
        TextView title = new TextView(this);
        title.setText(R.string.offline_title); title.setTextColor(Color.WHITE); title.setTextSize(22);
        title.setGravity(Gravity.CENTER);
        TextView message = new TextView(this);
        message.setText(R.string.offline_message); message.setTextColor(Color.rgb(170, 179, 191)); message.setTextSize(14);
        message.setGravity(Gravity.CENTER); message.setPadding(0, pad / 2, 0, pad);
        Button retry = new Button(this);
        retry.setText(R.string.retry);
        retry.setOnClickListener(v -> { offline.setVisibility(View.GONE); mainFrameFailed = false; webView.loadUrl(NavigationPolicy.START_URL); });
        panel.addView(title); panel.addView(message); panel.addView(retry);
        return panel;
    }

    private void showOffline() { mainFrameFailed = true; offline.setVisibility(View.VISIBLE); }
    private final class StudioClient extends WebViewClient {
        @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
            if (!request.isForMainFrame()) return false;
            Uri uri = request.getUrl();
            NavigationPolicy.Decision decision = NavigationPolicy.classify(uri.toString());
            if (decision == NavigationPolicy.Decision.INTERNAL) return false;
            if (decision == NavigationPolicy.Decision.EXTERNAL && request.hasGesture()) {
                try { startActivity(new Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE)); }
                catch (ActivityNotFoundException | SecurityException error) { Toast.makeText(MainActivity.this, R.string.external_unavailable, Toast.LENGTH_SHORT).show(); }
            }
            return true;
        }
        @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap favicon) {
            if (NavigationPolicy.classify(url) != NavigationPolicy.Decision.INTERNAL) { view.stopLoading(); showOffline(); return; }
            mainFrameFailed = false;
        }
        @Override public void onPageFinished(WebView view, String url) {
            if (!mainFrameFailed && NavigationPolicy.classify(url) == NavigationPolicy.Decision.INTERNAL) offline.setVisibility(View.GONE);
        }
        @Override public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
            if (request.isForMainFrame()) showOffline();
        }
        @Override public void onReceivedHttpError(WebView view, WebResourceRequest request, WebResourceResponse response) {
            if (request.isForMainFrame() && response.getStatusCode() >= 500) showOffline();
        }
        @Override public void onReceivedSslError(WebView view, SslErrorHandler handler, SslError error) {
            handler.cancel(); // Never proceed through a TLS certificate error.
            if (error.getUrl() != null && error.getUrl().equals(view.getUrl())) showOffline();
        }
    }

    private final class StudioChrome extends WebChromeClient {
        @Override public void onPermissionRequest(PermissionRequest request) { request.deny(); }
        @Override public void onGeolocationPermissionsShowPrompt(String origin, GeolocationPermissions.Callback callback) { callback.invoke(origin, false, false); }
        @Override public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
            if (view.getUrl() == null || NavigationPolicy.classify(view.getUrl()) != NavigationPolicy.Decision.INTERNAL) { callback.onReceiveValue(null); return true; }
            if (fileCallback != null) fileCallback.onReceiveValue(null);
            fileCallback = callback;
            Intent picker = new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("*/*");
            picker.putExtra(Intent.EXTRA_ALLOW_MULTIPLE, params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
            try { startActivityForResult(picker, FILE_PICKER); }
            catch (ActivityNotFoundException | SecurityException error) { fileCallback.onReceiveValue(null); fileCallback = null; Toast.makeText(MainActivity.this, R.string.external_unavailable, Toast.LENGTH_SHORT).show(); }
            return true;
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != FILE_PICKER || fileCallback == null) return;
        ArrayList<Uri> files = new ArrayList<>();
        if (resultCode == RESULT_OK && data != null) {
            ClipData clip = data.getClipData();
            if (clip != null) for (int i = 0; i < clip.getItemCount(); i++) addContentUri(files, clip.getItemAt(i).getUri());
            else addContentUri(files, data.getData());
        }
        fileCallback.onReceiveValue(files.isEmpty() ? null : files.toArray(new Uri[0]));
        fileCallback = null;
    }
    private static void addContentUri(ArrayList<Uri> files, Uri uri) { if (uri != null && "content".equals(uri.getScheme())) files.add(uri); }

    @Override public void onBackPressed() {
        if (offline.getVisibility() == View.VISIBLE) { offline.setVisibility(View.GONE); webView.loadUrl(NavigationPolicy.START_URL); }
        else if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }
    @Override protected void onDestroy() {
        if (fileCallback != null) { fileCallback.onReceiveValue(null); fileCallback = null; }
        webView.destroy();
        super.onDestroy();
    }
}
