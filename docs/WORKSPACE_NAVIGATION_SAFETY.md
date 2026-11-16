# Next Studio: focused navigation safety

The migration preview's overview card links to Bot management, Team and Hosting
now use the existing unsaved/pending-work warning, as does the empty bot-list
link. Choosing to stay keeps the workspace-name draft. A pending workspace
creation may still finish after leaving; the warning does not cancel a request.

Modified link clicks (Ctrl, Command, Shift or Alt) do not discard the original
page's draft or turn off its guard, because they need not leave that page. The
normal sidebar and page-finder handlers use the same rule. Test coverage uses
Chromium with an isolated synthetic API; modified events are dispatched and
their browser default is canceled, not tested as real new-tab flows.

This is not a comprehensive navigation or data-loss guarantee. Browser
back/forward within the Next SPA can bypass click guards; browser/tab close and
full-document navigation depend on the browser's `beforeunload` behavior.
Some browsers or embedded Telegram clients may suppress confirmations, and a
network operation can complete after leaving. Other navigation paths, focus,
mobile devices and assistive technology require independent review. No change
to legacy Studio, authorization, Telegram or production configuration is made.

## File Store rename drafts

In the Next preview, Cancel and choosing a different file now ask before
throwing away a changed filename. Declining keeps the draft; canceling an
unchanged draft needs no prompt and restores focus to its Rename button (or the
file-records heading if that button disappeared). The input receives focus when
opened. A pending rename disables its controls and uses the existing navigation
warning; a failed response retains the draft and warns that file and record
changes may be partial. These are best-effort browser interactions, not a
transaction, a retry guarantee or protection against browser history, crashes,
client-specific confirmation behavior or other sessions editing the same file.
