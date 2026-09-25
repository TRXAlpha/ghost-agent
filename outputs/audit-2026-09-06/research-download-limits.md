# Bounded source downloads — 6 September 2026

`fetch_source_text` now streams HTML with a default 2 MiB response-body cap. Callers can choose a positive integer cap up to 8 MiB. It checks declared Content-Length before downloading and counts received chunks when length is absent or unhelpful. An oversized or interrupted body yields no evidence, not a truncated document represented as complete source text.

Redirect handling is explicit: at most five redirects, each response closed without consuming its body. Missing/invalid destinations and unsupported URL schemes are declined. This avoids automatic redirect handling consuming an arbitrarily large intermediate body.

The adapter requests identity encoding and refuses compressed responses. This conservatively avoids decompression expansion before the byte gate but can reduce source availability. Only HTML/XHTML media types are accepted; PDF support remains absent. Successful results include download byte count and an explicit flag when the evidence text is shortened to the existing character budget.

Tests cover oversized declared and undeclared bodies, malformed lengths, compressed/non-HTML responses, redirect loops and invalid targets, response cleanup, interrupted downloads, and invalid budgets. A real loopback server supplies HTML without Content-Length: a small cap rejects it and a sufficient cap accepts it.

Full verification: **428 passed in 12.06 seconds**; compileall completed successfully.

Remaining boundaries: private-network/SSRF policy, DNS and redirect network authorization, whole-download deadlines, compressed-source support, PDF retrieval, and bounded scholarly/search responses. The timeout is still a socket timeout, not a total wall-clock deadline. A body-size cap is not a complete process-memory or CPU quota. Source-quality and exact-quotation gates remain unchanged.
