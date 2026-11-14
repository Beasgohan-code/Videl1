# Videl Studio for Android

This is a small Android WebView wrapper around the existing hosted Studio at
`https://videl1-frontend.vercel.app/app`, not a new native implementation or a
replacement backend. It requires Android 7.0 (API 24) or newer, a working
Android System WebView, internet access, and the existing Videl service. Its
content and availability follow that deployed website; this app does not deploy
or update the website.

## Sign in

The standalone app is not a Telegram Mini App and does not receive Telegram
`initData`. Send `/web` to the Videl bot in a private Telegram chat and use the
one-time sign-in key (the `l.…` portion after `#` in the link) in Studio's
sign-in field. Keys are single-use and expire; never share them. The app does
not register an Android deep link, so tapping the Telegram link opens a browser
rather than transferring the login into the app. Sign out in Studio when using
a shared device. Backend session durability and availability are unchanged.

## Build and get the APK

Local: install JDK 17, Android SDK platform 35 and build tools 35.0.0, accept
SDK licenses, set `ANDROID_HOME` (or create `local.properties`), then:

```sh
cd android
./gradlew testDebugUnitTest assembleDebug
# Windows: gradlew.bat testDebugUnitTest assembleDebug
```

The APK is `app/build/outputs/apk/debug/app-debug.apk`. The checked-in Gradle
wrapper is 8.11.1 with the official distribution SHA-256; Android Gradle
Plugin is pinned to 8.9.3. Dependency downloads require access to Google Maven,
Maven Central and Gradle distributions.

GitHub Actions workflow **Android debug APK** builds and uploads the
`videl-studio-debug-apk` artifact on matching changes pushed to this session's
branch or in a PR to `main`; the workflow can also be manually dispatched
once GitHub makes it available for dispatch (GitHub requires the workflow file
on the default branch). Open the successful run in **Actions**, download its
artifact, unzip it, then install the APK on an Android device. Artifacts expire
after seven days. The workflow file and app source must first be committed and
pushed before GitHub can run them; local uncommitted work is not visible to CI.
The first CI attempts failed during Android SDK setup before Gradle; logs were
not accessible from the sandbox. The revised workflow uses the Android SDK
preinstalled on GitHub’s Ubuntu 24.04 runner. A successful run and artifact
have not yet been confirmed.

This is a **debug-signed** build, not a Play Store or release-signed package.
The debug signing key on a fresh CI runner can differ between runs, so Android
may reject an update over an older build. Uninstalling the old build removes
its app data. Do not distribute it as a production release; stable release
signing, key custody and release publication need a separate approved setup.

## Behavior and limitations

- Only the exact Vercel HTTPS host is loaded in the main WebView; non-HTTPS and
  arbitrary intent schemes are blocked. User-initiated external HTTPS,
  Telegram and simple mail links open in an installed external app. Basic
  checks block some credential-bearing links, but cannot identify every secret
  embedded in an arbitrary URL. Never put secrets in external links. External
  pages do not share the app's WebView session, so authenticated links may fail.
- JavaScript and DOM storage are enabled because Studio requires them. There is
  no native JavaScript bridge. The website and its backend remain responsible
  for authentication, authorization and security; this wrapper does not make a
  compromised website safe. App data is not included in Android backup.
- File inputs use the Android document picker, including multiple selection.
  It does not provide camera capture, and file type filtering is not enforced
  by the picker. The server must validate uploaded files. Camera, microphone
  and geolocation WebView requests are denied.
- Downloads are not implemented in the wrapper (including blob downloads); a
  message directs users to a browser. Pop-up windows are not supported. Use a
  browser when these capabilities are required. Offline/retry, uploads, login,
  navigation and the live service still require device testing.
