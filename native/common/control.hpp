// Line-delimited JSON over the control socket (--control-fd). Every message is one object
// with a "type" field; see main.cpp for the protocol.
#pragma once

#include <nlohmann/json.hpp>

#include <string>
#include <vector>

namespace mt {

class Control {
 public:
  explicit Control(int fd) : fd_(fd) {}

  // False once the peer is gone.
  bool send(const nlohmann::json &msg);
  // Wait up to timeout_ms (-1: forever) for input, then return every complete message.
  // Lines that aren't JSON objects are dropped. Sets closed() on EOF or error.
  std::vector<nlohmann::json> poll(int timeout_ms);
  bool closed() const { return closed_; }

 private:
  int fd_;
  bool closed_ = false;
  std::string buf_;
};

}  // namespace mt
