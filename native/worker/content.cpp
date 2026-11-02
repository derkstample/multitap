#include "content.hpp"

#include <archive.h>
#include <archive_entry.h>

#include <algorithm>
#include <cctype>
#include <filesystem>
#include <fstream>
#include <memory>

namespace mt {

namespace {

std::string file_name(const std::string &path) {
  auto slash = path.find_last_of('/');
  return slash == std::string::npos ? path : path.substr(slash + 1);
}

// Index of the suffix dot in a file name, as pathlib sees it (none for ".bashrc" or "a.").
size_t suffix_dot(const std::string &name) {
  auto dot = name.find_last_of('.');
  if (dot == std::string::npos || dot == 0 || dot + 1 == name.size()) return std::string::npos;
  return dot;
}

bool has(const std::vector<std::string> &v, const std::string &s) {
  return std::find(v.begin(), v.end(), s) != v.end();
}

using ArchivePtr = std::unique_ptr<archive, decltype(&archive_read_free)>;

}  // namespace

std::string ext_of(const std::string &path) {
  std::string name = file_name(path);
  auto dot = suffix_dot(name);
  std::string ext = dot == std::string::npos ? "" : name.substr(dot + 1);
  std::transform(ext.begin(), ext.end(), ext.begin(), ::tolower);
  return ext;
}

std::string stem_of(const std::string &path) {
  std::string name = file_name(path);
  auto dot = suffix_dot(name);
  return dot == std::string::npos ? name : name.substr(0, dot);
}

std::string prepare_content(const std::string &path, const std::vector<std::string> &core_exts,
                            const std::string &tmpdir, bool block_extract) {
  std::string ext = ext_of(path);
  if (ext != "zip" || has(core_exts, ext) || block_extract) return path;

  const char *kNoMember = "Archive contains no file this core can load";
  ArchivePtr ar(archive_read_new(), archive_read_free);
  // The seekable reader walks the central directory, in zipfile.namelist() order.
  archive_read_support_format_zip_seekable(ar.get());
  if (archive_read_open_filename(ar.get(), path.c_str(), 1 << 16) != ARCHIVE_OK)
    throw ContentError(kNoMember);

  archive_entry *entry = nullptr;
  while (archive_read_next_header(ar.get(), &entry) == ARCHIVE_OK) {
    const char *raw = archive_entry_pathname_utf8(entry);
    if (!raw) raw = archive_entry_pathname(entry);
    std::string member = raw ? raw : "";
    if (member.empty() || member.back() == '/' || !has(core_exts, ext_of(member))) {
      archive_read_data_skip(ar.get());
      continue;
    }
    std::string target = (std::filesystem::path(tmpdir) / file_name(member)).string();
    std::ofstream out(target, std::ios::binary | std::ios::trunc);
    if (!out) throw std::runtime_error("Cannot write " + target);
    char buf[1 << 16];
    for (;;) {
      la_ssize_t n = archive_read_data(ar.get(), buf, sizeof buf);
      if (n < 0) throw ContentError(std::string("Cannot extract ") + member + ": " +
                                    archive_error_string(ar.get()));
      if (n == 0) break;
      out.write(buf, n);
    }
    if (!out.flush()) throw std::runtime_error("Cannot write " + target);
    return target;
  }
  throw ContentError(kNoMember);
}

}  // namespace mt
