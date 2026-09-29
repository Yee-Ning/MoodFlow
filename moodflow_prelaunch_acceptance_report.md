# MoodFlow V5.2 Public Browser Acceptance

Date: 2026-09-29 (Asia/Shanghai)

Application commit on ECS: `0fdf4f71dfa19a9a7fb57b04ed6b73719c02cd72`

Decision: **Deployment passed; full browser acceptance did not pass. Hold early-user invitation.**

The user narrowed the remaining work to V5.2 deployment and acceptance while product development is still underway. Browser exploration stopped at that point. The observations below came from the real public site, not fixtures; they are not a claim that unfinished product features are complete.

## Access and session

| Check | Observation | Result |
| --- | --- | --- |
| HTTPS/home | `https://47.83.162.191/` loaded without a browser certificate interstitial. | Pass |
| Netease QR login | The user scanned the displayed QR code; `/library` showed the authenticated account and 11 synced playlists. | Pass |
| Cookie flags | `JSESSIONID` metadata showed Secure, HttpOnly and SameSite=Lax. Its value was not read. | Pass |
| Refresh | Reloading the library restored the authenticated account after a brief loading state. | Pass |
| Service rebuild | Rebuilding Java invalidated the previous server-side login; another QR scan restored access. | Continuity across restart unverified |
| Logout | No browser logout check was completed. | Not verified |

## AI and public browser actions

| Scenario | Actual observation | Result |
| --- | --- | --- |
| GeneralChat | “什么是和声？” returned HTTP 200, `update → final → done`, one successful GeneralChat result and a complete visible answer on the final deployment. A concept-album question and open discussion also succeeded before the final logging-only deployment. | Pass for sampled queries |
| CurrentSong without song | “这首歌为什么听起来这么伤感？” returned `needs_input` and asked for a song. | Pass |
| Play Creep | “帮我播放 Radiohead 的 Creep” returned one successful ToolCalling result and exactly one `play_song` SSE action. The player showed Creep, a one-song queue and a pause control; the user confirmed playback was normal. A later intermittent `play() interrupted by pause()` status appeared. | Pass for observed action, with intermittent player status |
| CurrentSong with song | With Creep selected, the analysis request returned one successful CurrentSong result, no client action and a visible answer. | Pass |
| Search and lyrics | Search Radiohead and obtain Creep lyrics each returned a successful ToolCalling result and one tool call. The UI displayed the corresponding data. Lyrics are omitted from this report. | Pass |
| Favorite | “分析当前这首歌为什么这么伤感，然后收藏它” emitted one `set_liked` action. The player changed to “取消喜欢”. | Action executed; task split included an extra GeneralChat task |
| Worldline UI | A Radiohead session produced 9 nodes, 8 edges, 7 sources and visible citations. Refresh restored the checkpoint. Exploring Creep added Pablo Honey to the graph. | Partial pass |
| Worldline continuation | Exploring Pablo Honey failed with a merge alert; the earlier graph remained intact. A combined search-and-influence request returned `worldline: failed`. Several name variants required entity clarification. | Fail for complete relation workflow |
| Playlist action | Not exercised after the user narrowed scope. | Not verified |

## Multi-intent observations before scope narrowing

The requested ten-case browser matrix was not completed. Actual attempts:

| Case | Route/task and action result |
| --- | --- |
| Analyze song, then favorite | `current_song → general_chat → tool_calling`, all success, one `set_liked`. The middle GeneralChat task was not requested. |
| Search Creep, then play | One ToolCalling task with four tool calls; it emitted `play_song` **and an unrequested `set_liked`** action. Creep was already liked, so the visible liked state stayed on. This fails the no-wrong-side-effect gate. |
| Search Radiohead, then explore influence | ToolCalling success, Worldline failed, no client action. |
| Analyze song and explain shoegaze | CurrentSong then GeneralChat, both success, no client action. |
| CurrentSong + ToolCalling + GeneralChat | Three results in requested order, all success, one read-only search call, no client action. |
| “不要切歌，分析一下当前这首歌” | `needs_input`, no result and no action. The song did not switch, but the requested analysis was omitted. |

The ToolCalling + Worldline + GeneralChat attempt was interrupted when the user stopped product-feature testing. Same-route two/three-task cases and the remaining ambiguous-entity case were not completed. Earlier Worldline UI ambiguous names did ask for clarification.

## Acceptance limits

- The first four browser requests after the final logging deployment appeared in the `acceptance` telemetry bucket. Nine later requests, temporally matching our recreated browser tab tests, appeared in `production`. They **must not be presented as organic user traffic**. The browser marker/session classification needs a separate fix or controlled retest.
- Zero candidate dispatch counters support the V5 Shadow safety boundary. The unrequested favorite action was observed in the Legacy Primary response, not attributed to Shadow.
- Local automated tests cover timeout, unavailable API, invalid schema, low confidence, task conflict, missing SongContext, unsupported and negated-action paths. Production fault injection was not performed.
- Public-browser results verify deployment and selected paths, but the full browser gate remains open. Hold early-user invitation until the wrong-action and Worldline failures are resolved, telemetry tagging is reliable, and the remaining matrix is rerun.
