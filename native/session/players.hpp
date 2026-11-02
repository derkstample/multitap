// One multitap-worker process per player: spawn, control messages, and its shared memory.
#pragma once

#include <sys/types.h>

#include <deque>
#include <memory>
#include <optional>
#include <string>

#include "config.hpp"
#include "control.hpp"
#include "shm.hpp"

namespace mt::session {

int64_t now_ns();  // CLOCK_MONOTONIC

class Player {
 public:
  Player(int slot, const PlayerCfg &cfg) : slot(slot), cfg(cfg) {}
  ~Player();
  Player(const Player &) = delete;
  Player &operator=(const Player &) = delete;

  // Start the worker. Failures are recorded in `error` (the tile shows them).
  void spawn(const std::string &worker, const Config &c, bool audible);
  // Read pending control messages and notice a dead process. Returns true if anything changed.
  bool poll();
  void send(const nlohmann::json &msg);
  // Ask the worker to quit; kill() it if it is still running after the grace period.
  void quit();
  void kill();
  bool alive() const { return pid_ > 0; }

  // Loaded and still running: has shm, takes commands.
  bool ready() const { return loaded && !exited; }
  // Done loading one way or another (the start barrier waits for this).
  bool settled() const { return loaded || exited || !error.empty(); }

  const int slot;
  const PlayerCfg cfg;
  bool loaded = false, exited = false;
  std::string error;
  std::string core_name;
  double fps = 60.0, aspect = 4.0 / 3.0, sample_rate = 0.0;
  double frame_ms = 0.0, measured_fps = 0.0;  // from the worker's stats
  int64_t spawned_ns = 0, loaded_ns = 0;
  std::deque<std::string> logs;  // most recent last, capped

  std::optional<shm::Video> video;
  std::optional<shm::Input> input;
  std::optional<shm::Audio> audio;

 private:
  void open_shm(const std::string &prefix);
  void reap(bool block);

  pid_t pid_ = -1;
  std::unique_ptr<Control> ctl_;
  int fd_ = -1;
  std::string shm_prefix_;
  shm::Mapping video_map_, input_map_, audio_map_;
};

}  // namespace mt::session
