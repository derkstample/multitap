#include "input.hpp"

#include <algorithm>
#include <utility>

namespace mt::session {

namespace {

constexpr int kUp = 4, kDown = 5, kLeft = 6, kRight = 7;

// Qt doesn't tell left and right modifiers apart, so bindings always name the left one.
SDL_Keycode normalize(SDL_Keycode k) {
  switch (k) {
    case SDLK_RSHIFT: return SDLK_LSHIFT;
    case SDLK_RCTRL: return SDLK_LCTRL;
    case SDLK_RALT: return SDLK_LALT;
    case SDLK_RGUI: return SDLK_LGUI;
    default: return k;
  }
}

// "vvvv:pppp" from an SDL GUID string, or "" when it carries no vendor/product.
std::string vendor_product(const std::string &guid) {
  if (guid.size() != 32 || guid.compare(4, 4, "0000") == 0) return "";
  return guid.substr(8, 4) + ":" + guid.substr(16, 4);
}

}  // namespace

std::set<std::string> active_elements(const std::set<std::string> &buttons,
                                      const std::map<std::string, int> &axes) {
  std::set<std::string> out;
  for (const auto &b : buttons) out.insert("button:" + b);
  for (const auto &[name, v] : axes) {
    if (v > kAxisThreshold) out.insert("axis:" + name + "+");
    else if (v < -kAxisThreshold) out.insert("axis:" + name + "-");
  }
  return out;
}

PadBits pad_state_bits(const std::set<std::string> &buttons, const std::map<std::string, int> &axes,
                       const std::map<std::string, int> &bindings) {
  PadBits out;
  auto active = active_elements(buttons, axes);
  for (const auto &[element, id] : bindings)
    if (active.count(element)) out.bits |= 1u << id;
  auto axis = [&](const char *n) {
    auto it = axes.find(n);
    return it == axes.end() ? 0 : it->second;
  };
  int lx = axis("leftx"), ly = axis("lefty");
  if (lx < -kAxisThreshold) out.bits |= 1u << kLeft;
  if (lx > kAxisThreshold) out.bits |= 1u << kRight;
  if (ly < -kAxisThreshold) out.bits |= 1u << kUp;
  if (ly > kAxisThreshold) out.bits |= 1u << kDown;
  out.analog[0] = lx;
  out.analog[1] = ly;
  out.analog[2] = axis("rightx");
  out.analog[3] = axis("righty");
  return out;
}

Input::Input(std::vector<std::unique_ptr<Player>> &players)
    : players_(players), pad_of_(players.size(), -1), written_(players.size(), ~0u) {}

Input::~Input() {
  for (auto &[id, pad] : pads_) SDL_GameControllerClose(pad.gc);
}

void Input::open_pad(int index) {
  SDL_GameController *gc = SDL_GameControllerOpen(index);
  if (!gc) return;
  SDL_Joystick *joy = SDL_GameControllerGetJoystick(gc);
  SDL_JoystickID id = SDL_JoystickInstanceID(joy);
  if (pads_.count(id)) {
    SDL_GameControllerClose(gc);
    return;
  }
  char buf[64];
  SDL_JoystickGetGUIDString(SDL_JoystickGetGUID(joy), buf, sizeof buf);
  Pad pad;
  pad.gc = gc;
  pad.id = id;
  pad.guid = buf;
  const char *name = SDL_GameControllerName(gc);
  pad.name = name ? name : "Controller";
  // Same identity rule as the launcher: the lowest ordinal not taken by a pad with this GUID.
  std::set<int> used;
  for (const auto &[_, p] : pads_)
    if (p.guid == pad.guid) used.insert(p.ordinal);
  while (used.count(pad.ordinal)) pad.ordinal++;
  pad.key = "pad:" + pad.guid + ":" + std::to_string(pad.ordinal);
  pads_[id] = std::move(pad);
  devices_changed_ = true;
  assign_pads();
}

void Input::close_pad(SDL_JoystickID id) {
  auto it = pads_.find(id);
  if (it == pads_.end()) return;
  SDL_GameControllerClose(it->second.gc);
  pads_.erase(it);
  devices_changed_ = true;
  assign_pads();
}

// Match each pad player to a connected controller: by exact key first, then (the launcher's
// SDL may name the same pad differently) by vendor/product and ordinal among those.
void Input::assign_pads() {
  std::set<SDL_JoystickID> taken;
  std::vector<SDL_JoystickID> next(players_.size(), -1);
  for (size_t s = 0; s < players_.size(); s++) {
    const Device &d = players_[s]->cfg.device;
    if (d.kind != "pad") continue;
    for (const auto &[id, pad] : pads_)
      if (pad.key == d.key() && !taken.count(id)) {
        next[s] = id;
        taken.insert(id);
        break;
      }
  }
  for (size_t s = 0; s < players_.size(); s++) {
    const Device &d = players_[s]->cfg.device;
    std::string vp = vendor_product(d.guid);
    if (d.kind != "pad" || next[s] >= 0 || vp.empty()) continue;
    int seen = 0;
    for (const auto &[id, pad] : pads_) {
      if (vendor_product(pad.guid) != vp) continue;
      if (seen++ == d.ordinal && !taken.count(id)) {
        next[s] = id;
        taken.insert(id);
        break;
      }
    }
  }
  for (size_t s = 0; s < players_.size(); s++) {
    if (next[s] == pad_of_[s]) continue;
    pad_of_[s] = next[s];
    write_pad(int(s));
  }
}

Hotkey Input::handle(const SDL_Event &e) {
  switch (e.type) {
    case SDL_KEYDOWN:
    case SDL_KEYUP: {
      if (e.key.repeat) return Hotkey::None;
      SDL_Keycode k = normalize(e.key.keysym.sym);
      if (e.type == SDL_KEYDOWN) {
        switch (k) {
          case SDLK_ESCAPE: return Hotkey::Escape;
          case SDLK_p: return Hotkey::Pause;
          case SDLK_r: return Hotkey::Reset;
          case SDLK_F1: return Hotkey::ToggleOsd;
          default: held_.insert(k);
        }
      } else {
        held_.erase(k);
      }
      write_keyboard();
      return Hotkey::None;
    }
    case SDL_CONTROLLERDEVICEADDED:
      open_pad(e.cdevice.which);
      return Hotkey::None;
    case SDL_CONTROLLERDEVICEREMOVED:
      close_pad(e.cdevice.which);
      return Hotkey::None;
    case SDL_CONTROLLERBUTTONDOWN:
    case SDL_CONTROLLERBUTTONUP: {
      auto it = pads_.find(e.cbutton.which);
      const char *name = SDL_GameControllerGetStringForButton(SDL_GameControllerButton(e.cbutton.button));
      if (it == pads_.end() || !name) return Hotkey::None;
      if (e.type == SDL_CONTROLLERBUTTONDOWN) it->second.buttons.insert(name);
      else it->second.buttons.erase(name);
      break;
    }
    case SDL_CONTROLLERAXISMOTION: {
      auto it = pads_.find(e.caxis.which);
      const char *name = SDL_GameControllerGetStringForAxis(SDL_GameControllerAxis(e.caxis.axis));
      if (it == pads_.end() || !name) return Hotkey::None;
      it->second.axes[name] = e.caxis.value;
      break;
    }
    default:
      return Hotkey::None;
  }
  // A controller event: refresh the players on that pad.
  SDL_JoystickID id = e.type == SDL_CONTROLLERAXISMOTION ? e.caxis.which : e.cbutton.which;
  for (size_t s = 0; s < players_.size(); s++)
    if (pad_of_[s] == id) write_pad(int(s));
  return Hotkey::None;
}

void Input::clear_keyboard() {
  held_.clear();
  write_keyboard();
}

void Input::write_keyboard() {
  for (size_t s = 0; s < players_.size(); s++) {
    const PlayerCfg &cfg = players_[s]->cfg;
    if (cfg.device.kind != "keyboard") continue;
    uint32_t bits = 0;
    for (SDL_Keycode k : held_)
      if (auto it = cfg.keys.find(k); it != cfg.keys.end()) bits |= 1u << it->second;
    write(int(s), bits, nullptr);
  }
}

void Input::write_pad(int slot) {
  auto it = pads_.find(pad_of_[slot]);
  if (it == pads_.end()) {
    int32_t zero[4] = {0, 0, 0, 0};
    write(slot, 0, zero);
    return;
  }
  PadBits pb = pad_state_bits(it->second.buttons, it->second.axes, players_[slot]->cfg.pad);
  write(slot, pb.bits, pb.analog);
}

void Input::write(int slot, uint32_t bits, const int32_t *analog) {
  Player &p = *players_[slot];
  if (!p.input) return;
  if (analog)
    for (int i = 0; i < 4; i++) p.input->set_analog(i, analog[i]);
  if (bits != written_[slot] || analog) {
    p.input->set_held(bits);
    written_[slot] = bits;
  }
}

void Input::refresh(int slot) {
  written_[slot] = ~0u;
  if (players_[slot]->cfg.device.kind == "keyboard") write_keyboard();
  else write_pad(slot);
}

bool Input::take_devices_changed() { return std::exchange(devices_changed_, false); }

std::set<int> Input::missing_slots() const {
  std::set<int> out;
  for (size_t s = 0; s < players_.size(); s++)
    if (players_[s]->cfg.device.kind == "pad" && pad_of_[s] < 0) out.insert(int(s));
  return out;
}

}  // namespace mt::session
