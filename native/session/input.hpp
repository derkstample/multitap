// Keyboard and SDL GameController input, turned into RetroPad state in each player's input shm.
#pragma once

#include <SDL.h>

#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "players.hpp"

namespace mt::session {

constexpr int kAxisThreshold = 16000;

enum class Hotkey { None, Escape, Pause, Reset, ToggleOsd };

// "button:a", "axis:lefttrigger+" elements that are active (port of devices.active_elements).
std::set<std::string> active_elements(const std::set<std::string> &buttons,
                                      const std::map<std::string, int> &axes);

struct PadBits {
  uint32_t bits = 0;
  int32_t analog[4] = {0, 0, 0, 0};
};
// Raw controller state -> RetroPad mask and analog values (port of devices.pad_state_bits);
// the left stick doubles as the d-pad.
PadBits pad_state_bits(const std::set<std::string> &buttons, const std::map<std::string, int> &axes,
                       const std::map<std::string, int> &bindings);

class Input {
 public:
  explicit Input(std::vector<std::unique_ptr<Player>> &players);
  ~Input();

  // Handles key and controller events; returns the hotkey a key press triggered, if any.
  Hotkey handle(const SDL_Event &e);
  // Release every key (the window lost focus).
  void clear_keyboard();
  // Push the current state to a player whose shm just appeared.
  void refresh(int slot);
  // True once since the last call if a controller came or went.
  bool take_devices_changed();
  // Tiles whose controller isn't connected.
  std::set<int> missing_slots() const;

 private:
  struct Pad {
    SDL_GameController *gc = nullptr;
    SDL_JoystickID id = -1;
    std::string guid, name, key;
    int ordinal = 0;
    std::set<std::string> buttons;
    std::map<std::string, int> axes;
  };

  void open_pad(int device_index);
  void close_pad(SDL_JoystickID id);
  void assign_pads();
  void write_keyboard();
  void write_pad(int slot);
  void write(int slot, uint32_t bits, const int32_t *analog);

  std::vector<std::unique_ptr<Player>> &players_;
  std::map<SDL_JoystickID, Pad> pads_;
  std::vector<SDL_JoystickID> pad_of_;  // per slot; -1 = not connected (pad players only)
  std::set<SDL_Keycode> held_;
  std::vector<uint32_t> written_;
  bool devices_changed_ = false;
};

}  // namespace mt::session
