#include "players.hpp"

#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <sys/socket.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>
#include <iostream>
#include <vector>

extern char **environ;

namespace mt::session {

constexpr size_t kMaxLogs = 50;

int64_t now_ns() {
  timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return int64_t(ts.tv_sec) * 1'000'000'000 + ts.tv_nsec;
}

Player::~Player() {
  if (alive()) kill();
  if (fd_ >= 0) close(fd_);
}

void Player::spawn(const std::string &worker, const Config &c, bool audible) {
  spawned_ns = now_ns();
  int sv[2];
  if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, sv) != 0) {
    error = std::string("socketpair: ") + std::strerror(errno);
    return;
  }
  // The child keeps sv[1] across exec. Workers are spawned one at a time and each child end is
  // closed right after, so no worker inherits another's socket.
  fcntl(sv[1], F_SETFD, 0);
  shm_prefix_ = "multitap-" + std::to_string(getpid()) + "-" + std::to_string(slot);
  std::string name = cfg.name.empty() ? "Player " + std::to_string(cfg.index + 1) : cfg.name;
  std::vector<std::string> args = {worker,
                                   "--control-fd", std::to_string(sv[1]),
                                   "--core", c.core_path,
                                   "--rom", c.rom_path,
                                   "--system-dir", c.system_dir,
                                   "--save-dir", cfg.save_dir,
                                   "--slot", std::to_string(slot),
                                   "--shm-prefix", shm_prefix_,
                                   "--username", name};
  if (audible) args.push_back("--audible");
  std::vector<char *> argv;
  for (auto &a : args) argv.push_back(a.data());
  argv.push_back(nullptr);
  int rc = posix_spawn(&pid_, worker.c_str(), nullptr, nullptr, argv.data(), environ);
  close(sv[1]);
  if (rc != 0) {
    pid_ = -1;
    close(sv[0]);
    error = "cannot start " + worker + ": " + std::strerror(rc);
    return;
  }
  fd_ = sv[0];
  fcntl(fd_, F_SETFL, fcntl(fd_, F_GETFL) | O_NONBLOCK);
  ctl_ = std::make_unique<Control>(fd_);
}

void Player::open_shm(const std::string &prefix) {
  video_map_ = shm::Mapping::open(prefix + "-video");
  input_map_ = shm::Mapping::open(prefix + "-input");
  audio_map_ = shm::Mapping::open(prefix + "-audio");
  video.emplace(video_map_.data());
  input.emplace(input_map_.data());
  audio.emplace(audio_map_.data());
  if (!video->valid() || !input->valid() || !audio->valid())
    throw std::runtime_error("shared memory has the wrong version");
}

bool Player::poll() {
  if (!ctl_) return false;
  if (exited) {
    reap(false);
    return false;
  }
  bool changed = false;
  for (const auto &msg : ctl_->poll(0)) {
    std::string type = msg.value("type", "");
    changed = true;
    if (type == "loaded") {
      try {
        open_shm(msg.value("shm", shm_prefix_));
        loaded = true;
        loaded_ns = now_ns();
        core_name = msg.value("name", "");
        fps = msg.value("fps", 60.0);
        aspect = msg.value("aspect", 4.0 / 3.0);
        sample_rate = msg.value("sample_rate", 0.0);
      } catch (const std::exception &e) {
        error = e.what();
        quit();
      }
    } else if (type == "error") {
      error = msg.value("message", "unknown error");
    } else if (type == "log") {
      logs.push_back(msg.value("message", ""));
      if (logs.size() > kMaxLogs) logs.pop_front();
      std::cerr << "P" << cfg.index + 1 << ": " << logs.back() << "\n";
    } else if (type == "stats") {
      frame_ms = msg.value("frame_ms", 0.0);
      measured_fps = msg.value("fps", 0.0);
    } else if (type == "exited") {
      exited = true;
    }
  }
  if (ctl_->closed()) {
    exited = true;
    changed = true;
  }
  if (exited) reap(false);
  if (exited && !loaded && error.empty()) {
    error = "Emulator process exited unexpectedly";
  }
  return changed;
}

void Player::send(const nlohmann::json &msg) {
  if (ctl_ && !exited) ctl_->send(msg);
}

void Player::quit() { send({{"type", "quit"}}); }

void Player::reap(bool block) {
  if (pid_ <= 0) return;
  int status = 0;
  pid_t r = waitpid(pid_, &status, block ? 0 : WNOHANG);
  if (r == 0) return;  // closed its socket but hasn't exited yet
  pid_ = -1;
  if (r > 0 && WIFSIGNALED(status) && WTERMSIG(status) != SIGKILL && error.empty())
    error = "Emulator crashed (" + std::string(strsignal(WTERMSIG(status))) + ")";
}

void Player::kill() {
  if (pid_ > 0) {
    ::kill(pid_, SIGKILL);
    reap(true);
  }
  exited = true;
  // A killed worker never unlinked its shm.
  for (const char *s : {"-video", "-input", "-audio"}) shm_unlink(("/" + shm_prefix_ + s).c_str());
}

}  // namespace mt::session
