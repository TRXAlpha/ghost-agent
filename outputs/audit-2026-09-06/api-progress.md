# Local API boundary hardening — 6 September 2026

The HTTP transport now requires a loopback Host with the server's actual port and rejects foreign/null/duplicate Origins and cross-site browser requests. Authenticated API requests check their bearer token before reading the body. The static dashboard shell contains no user state and remains accessible; snapshot data still requires authentication when configured.

POST bodies require one nonnegative Content-Length, JSON content type, and a maximum of 64 KiB. Transfer encoding is refused. Connections have a five-second socket timeout (not an overall request deadline). Invalid UTF-8, malformed JSON, excessive nesting, non-object bodies, and nonfinite JSON values are rejected. Goal/observation inputs receive explicit type/range checks. JSON responses prohibit caching/sniffing; dashboard responses also prohibit framing.

`create_server` exposes an explicit shutdown lifecycle for tests and embedders. IPv6 uses an IPv6 socket; localhost binds the literal IPv4 loopback address, avoiding name-resolution dependence. Public bindings remain refused.

Tests exercise actual loopback HTTP requests, not just direct dispatch: same-origin and local clients, foreign origins, DNS-rebinding Host values, duplicate security headers, framing/size/content-type rejection, malformed data, authentication, and finite observation values.

Full suite: **316 passed in 9.71 seconds**. Compileall completed successfully.

## Client admission follow-up

The server now admits at most eight concurrent handlers by default. Embedded callers can choose an integer limit from 1 to 64. A nonblocking semaphore is acquired before thread creation; excess connections are closed without reading input or blocking the accept loop. Slots are released on normal completion, handler exceptions, and thread-start failures.

Regression tests hold one real loopback connection, verify an excess connection is closed, then verify service recovery after release. Separate injected-failure tests exercise handler and thread-start cleanup. Latest full suite: **325 passed in 10.08 seconds**; compileall completed successfully.

## Slow-client read deadline

Headers and body now share a monotonic 15-second read deadline. A small raw socket reader sets each receive timeout to the lesser of five seconds and the remaining total budget. Receiving partial data never extends that deadline. No timer thread is needed. Embedded callers may configure `read_timeout` from 0.05 to 300 seconds. Body-read expiry returns HTTP 408; header-read expiry closes the connection. Response writes retain a separate five-second timeout.

Real loopback tests continuously send bytes during incomplete headers and bodies. Both connections expire despite that activity, release their admission slot, and permit a subsequent valid request. Invalid deadlines are also rejected.

The first full run exposed an early-refusal test race on Windows: separate header/body sends could race connection closure, producing a connection reset instead of a readable status. Complete-request status tests now submit one complete wire request; a separate header-only test verifies immediate rejection without waiting for a body and confirms no goal is created. This does not promise every rejected client receives a response before disconnect.

Latest verification: **335 tests passed in 9.99 seconds**, compileall succeeded, and the **51 API tests passed again** separately in 2.58 seconds.

Remaining work: overall SDK execution deadlines, rate limits, audit events, secure dashboard token entry, and expanded control-plane pages. The read deadline does not cancel processing after the request is read. Tokenless mode still trusts local processes able to send arbitrary HTTP headers. This is not a public-network server or an OS security boundary. The tests do not certify browser-engine behavior, all denial-of-service cases, or the entire SDK/persistence layer.
