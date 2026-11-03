// Content handling: ROM loading and zip extraction.
#pragma once

#include <stdexcept>
#include <string>
#include <vector>

namespace mt {

// Bad content the core never saw (Python raises ValueError for these).
struct ContentError : std::runtime_error {
  using std::runtime_error::runtime_error;
};

// Path(path).suffix, lower case and without the dot ("" if none).
std::string ext_of(const std::string &path);
// Path(path).stem: the file name without its last suffix.
std::string stem_of(const std::string &path);

// The file to hand to the core: `path` itself, or its first loadable member extracted into
// `tmpdir` when it is a zip the core can't open itself.
std::string prepare_content(const std::string &path, const std::vector<std::string> &core_exts,
                            const std::string &tmpdir, bool block_extract);

}  // namespace mt
