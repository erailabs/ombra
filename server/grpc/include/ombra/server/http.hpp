// SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0 OR LicenseRef-Ombra-Commercial
// Minimal HTTP/1.1 request parsing for the REST admin port. Network-facing, so it is small
// and strict: the header block is capped, one request per connection, a request body only with
// Content-Length and at most kMaxHttpBodyBytes (no chunked bodies).
#pragma once

#include <cstddef>
#include <string>
#include <string_view>

namespace ombra::server {

inline constexpr std::size_t kMaxHttpHeaderBytes = 8 * 1024;

inline constexpr std::size_t kMaxHttpBodyBytes = 64 * 1024;

struct HttpRequest {
  std::string method, path;
  std::string authorization;  // value of the Authorization header, empty if absent
  std::string request_id;     // value of X-Request-Id, empty if absent (not yet validated)
  std::size_t content_length = 0;
  std::string body;  // filled by the caller once content_length bytes have arrived
};

enum class HttpParse { Incomplete, Ok, TooLarge, BodyTooLarge, Bad };

// `buf` holds the bytes received so far. Incomplete: the blank line ending the headers has not
// arrived and the cap is not reached. TooLarge: the header block exceeds kMaxHttpHeaderBytes.
// BodyTooLarge: Content-Length above kMaxHttpBodyBytes. Bad: malformed request line or header, a
// bad or repeated Content-Length, Transfer-Encoding, or a version other than HTTP/1.0 or 1.1.
// Ok sets `content_length` and does not read the body; `header_bytes` (if not null) gets the size
// of the header block including the blank line, so the caller knows where the body starts.
HttpParse parse_http_request(std::string_view buf, HttpRequest* out,
                             std::size_t* header_bytes = nullptr);

// A complete response with Connection: close.
// `extra_headers` is zero or more complete "Name: value\r\n" lines (no user input without
// checking).
std::string http_response(int status, std::string_view body,
                          std::string_view content_type = "application/json",
                          std::string_view extra_headers = {});

}  // namespace ombra::server
