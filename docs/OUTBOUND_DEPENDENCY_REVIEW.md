# Outbound and third-party review (partial)

This is a source-level inventory of selected paths, **not** a comprehensive dependency, license, vulnerability, privacy or production security audit. No provider was contacted to verify its policy or availability. OWASP recommends inventorying third-party and transitive components ([component analysis](https://owasp.org/www-community/Component_Analysis)) and validating outbound destinations, redirects and DNS at the point of connection ([SSRF prevention](https://owasp.org/Top10/2021/A10_2021-Server-Side_Request_Forgery_(SSRF)/)).

## Observed external dependencies

| Surface | Current source behavior | Disclosure / unresolved risk |
| --- | --- | --- |
| Telegram | WzGram handles incoming updates; aiogram sends Bot API calls; retained dashboard/detail snapshots can use a configured server or beta endpoint. | No widget-query poller remains. Snapshot delivery/client compatibility still depends on the configured Telegram endpoint and client; no live production delivery is implied. |
| MongoDB | Bot/platform records rely on configured MongoDB and multiple repositories. | Not verified with a real multi-worker cluster, failover, permissions or backup recovery. |
| Inline utilities | `core/inline.py` sends URL queries to is.gd and TinyURL automatically; QR images use `api.qrserver.com`, with input in the URL. | Input can contain sensitive data; typing an inline query can disclose it to providers without a separate confirmation. This review does **not** change inline behavior. Avoid sensitive input; design explicit opt-in and a local/Telegram-hosted QR flow. |
| `/short` and `/upload` | Shortening passes the full URL to external shorteners. Uploading sends file bytes to freeimage.host when configured, otherwise catbox.moe, to create a public URL. | Provider retention, access and terms are unverified. Do not send secrets; a public upload can be accessible to anyone holding its link. |
| Source/plugin fetching | Owner-oriented installer checks a URL before its default client follows redirects; plugin activation runs code in the bot process. | Redirects and DNS may reach a different address after validation; plugin code is not independently sandboxed. Do not install untrusted code. This path is not fixed by the media change. |
| Platform adapters | Download providers perform URL/DNS checks and revalidate redirects. | Existing pre-check and connection may resolve separately; private-address overrides exist. Other paths need connection-time DNS enforcement, redirect review and network egress controls. No blanket SSRF guarantee. |
| Builds | Python direct dependencies are pinned; the Dockerfile installs transitive dependencies and upgrades pip, and the base image is specified by tag. | Direct pins are not a complete lockfile or SBOM. Hashes, transitive provenance, licenses, vulnerability scanning and immutable image digest need review; no vulnerability or license clearance is implied. |

## Scoped change: remote `/mediainfo` header fetch

The previous fetch followed redirects before validating the final URL, and DNS pre-checks were separate from actual connections. The new helper rejects private/reserved literals and ambiguous numeric forms, URL userinfo, and unsafe schemes; checks **each** redirect before following; uses a DNS resolver that rejects mixed/private answers and supplies the validated IPs to aiohttp; disables environment proxies and DNS caching; keeps the existing Range and byte cap; and hides exception text that can contain signed URLs. The public-only policy is independent of `LEECH_ALLOW_PRIVATE` for this command. URLs with signed query strings remain supported; fragments are not sent. `ffprobe` is restricted to `file,pipe` protocols, which can disable network-dependent playlists.

**Limits:** This protects the application HTTP header fetch and blocks ffprobe's network protocols; it is not a general sandbox. ffprobe still parses untrusted data and may access local file references. Media parsing needs separate filesystem/process sandboxing, resource limits, egress controls and independent adversarial testing. Other download, plugin, preview, shortener, cloud-provider and webapp paths remain out of scope. The tests use fake DNS and a deliberately local HTTP fixture; they do not validate live Internet infrastructure, every DNS implementation or production network policy.

## Frontend lockfile advisory scan (partial)

`npm audit --json` against the checked-in `frontend-platform` lockfile and the
registry advisory data returned **3 affected package entries**: 1 critical,
1 high and 1 low. `npm ls` resolved Astro 6.4.8, Astro's sharp 0.34.5 and
esbuild 0.27.7. This is an advisory match, not evidence that any particular
application or deployed service is exploitable. The legacy production front
door is separate; deployment versions and actual exposure were not verified.

- Astro matches multiple advisories, including [AVIF image optimization RCE](https://github.com/advisories/GHSA-26w7-cxv4-gfx2), [spread-attribute XSS](https://github.com/advisories/GHSA-f48w-9m4c-m7f5), and [dynamic-tag XSS](https://github.com/advisories/GHSA-mh32-jxcr-mh9f).
- The Astro sharp dependency matches [libvips](https://github.com/advisories/GHSA-f88m-g3jw-g9cj), [libheif](https://github.com/advisories/GHSA-rgj7-g3m4-5g8c), and [librsvg](https://github.com/advisories/GHSA-wq5f-xc86-pv6w) advisories.
- esbuild matches a [Windows development-server arbitrary file read advisory](https://github.com/advisories/GHSA-g7r4-m6w7-qqqr).

Treat a public Astro preview or cutover as blocked pending applicability review,
a patched compatible dependency set, regression/build testing and a fresh
scan. The audit's suggested major-version change is not proof that every listed
advisory is fixed; do not use `npm audit fix --force` as an unreviewed fix. The
scan does not cover Python, containers, all runtime routes or license risks.

### Follow-up: tested Astro dependency update

The preview lockfile now uses Astro **7.4.12**, `@astrojs/react` **7.1.9** and
Vite **8.3.8**; the resolved sharp is **0.35.5**, and the compatible optional
esbuild peer is **0.28.2**. A fresh `npm ci` and `npm audit --json` on that
lockfile returned **zero known vulnerabilities** from the registry advisory
data at the time of the scan, including no matches for the entries above. This
is not a security certification or an independent vulnerability assessment.
The audit is not a review of Python, native media libraries, runtime network
exposure, deployment configuration or all licenses. Astro, `@astrojs/react`,
Vite and esbuild report MIT licensing in registry metadata; the complete
transitive license and provenance inventory remains open.

Astro 7 requires Node **22.23.4 or newer**; the frontend workspace engine and
`.nvmrc` now reflect that. Both frontend typechecks/builds and built-static-site
Chromium checks passed locally. The Next Studio suite runs with a synthetic API,
not a live service. The dedicated frontend CI job installs locked dependencies,
runs an advisory gate for high/critical matches, builds both workspaces and
tests the built public site; CI still depends on registry and browser downloads.
Review major-version changes and real deployment routing before approving any
cutover. This change does not upgrade, deploy or attest the legacy production
front door or its dependencies.
