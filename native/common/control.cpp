#include "control.hpp"

#include <poll.h>
#include <unistd.h>

#include <cerrno>

namespace mt {

bool Control::send(const nlohmann::json &msg) {
  if (closed_) return false;
  // Invalid UTF-8 from a core's log must not abort the dump.
  std::string line = msg.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace) + "\n";
  const char *p = line.data();
  size_t left = line.size();
  while (left) {
    ssize_t n = ::write(fd_, p, left);
    if (n < 0 && errno == EINTR) continue;
    if (n <= 0) {
      closed_ = true;
      return false;
    }
    p += n;
    left -= size_t(n);
  }
  return true;
}

std::vector<nlohmann::json> Control::poll(int timeout_ms) {
  std::vector<nlohmann::json> out;
  if (closed_) return out;
  pollfd pfd{fd_, POLLIN, 0};
  int r = ::poll(&pfd, 1, timeout_ms);
  if (r <= 0) return out;  // timeout, or EINTR (the caller re-checks its flags)
  char chunk[4096];
  ssize_t n = ::read(fd_, chunk, sizeof chunk);
  if (n < 0 && (errno == EINTR || errno == EAGAIN)) return out;
  if (n <= 0) {
    closed_ = true;
    return out;
  }
  buf_.append(chunk, size_t(n));
  size_t start = 0;
  for (size_t nl; (nl = buf_.find('\n', start)) != std::string::npos; start = nl + 1) {
    auto msg = nlohmann::json::parse(buf_.begin() + long(start), buf_.begin() + long(nl),
                                     nullptr, false);
    if (msg.is_object() && msg.contains("type")) out.push_back(std::move(msg));
  }
  buf_.erase(0, start);
  return out;
}

}  // namespace mt
