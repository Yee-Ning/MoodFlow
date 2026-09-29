# MoodFlow V5.2 Pre-Launch Browser Acceptance

Date: 2026-09-29 (Asia/Shanghai)

Status: **IN PROGRESS — not a Release Candidate pass**. Browser observations below were made against the pre-deployment ECS revision `4f3c72199815b74a29b30cd6bb578cf9408ffecd`. They must not be counted as post-deployment V5 acceptance.

## Public browser evidence before V5 deployment

| Check | Observation | Status |
| --- | --- | --- |
| HTTPS/home page | `https://47.83.162.191/` loaded without a browser certificate warning; entrance opened the login page. | Observed on old revision |
| Netease QR login | User scanned the displayed QR code; the browser reached `/library`, showed the authenticated account control and 11 synced playlists. | Observed on old revision |
| Session cookie | Browser cookie metadata for `JSESSIONID`: `Secure=true`, `HttpOnly=true`, `SameSite=Lax`, host `47.83.162.191`, path `/`. The cookie value was neither read nor recorded. | Observed on old revision |
| Refresh persistence | Reloading `/library` briefly showed loading state, then restored the authenticated account and library. | Observed on old revision |
| GeneralChat | In the visible AI panel, `什么是和声？` received a complete music-theory explanation; the UI visibly moved through loading to a completed answer. | Observed on old revision |

## Post-deployment browser acceptance

No V5 deployment had completed at the time of this draft. Therefore CurrentSong with/without a song, Worldline graph/citations/session refresh, ToolCalling search/lyrics/play/favorite/playlist actions, ten multi-intent browser scenarios, triple-route conflict, fallback injections, logout, responsive/interaction checks and post-test health are **NOT YET VERIFIED**. The old-revision browser observations above are not used to pass any of these gates.

The first browser session used the user's QR scan and did not bypass authentication or certificate checks. Browser automation later became unavailable while loading its request-header policy; further browser steps require the tool to recover. The user's Alibaba Cloud Workbench shell remained connected, so this browser-tool issue does not indicate ECS downtime.

## Public testing decision

**HOLD.** The required post-deployment browser and ECS acceptance gates remain open. Do not invite early users or count acceptance traffic as organic production shadow traffic yet.
