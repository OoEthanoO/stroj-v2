// Shim for libstdc++'s <bits/stdc++.h>.
//
// Practically every competitive submission opens with this include, but it is a
// GCC extension — libc++ (Apple clang) has no such header. Rather than make
// everyone rewrite their includes, the judge puts this directory on the C++
// include path so the habit keeps working.
#pragma once

// C library
#include <cassert>
#include <cctype>
#include <cerrno>
#include <cfloat>
#include <climits>
#include <clocale>
#include <cmath>
#include <csetjmp>
#include <csignal>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <cwchar>
#include <cwctype>

// Containers
#include <array>
#include <bitset>
#include <deque>
#include <forward_list>
#include <list>
#include <map>
#include <queue>
#include <set>
#include <stack>
#include <unordered_map>
#include <unordered_set>
#include <vector>

// General utilities and algorithms
#include <algorithm>
#include <bit>
#include <chrono>
#include <complex>
#include <exception>
#include <functional>
#include <initializer_list>
#include <iterator>
#include <limits>
#include <memory>
#include <new>
#include <numeric>
#include <optional>
#include <random>
#include <ratio>
#include <stdexcept>
#include <string>
#include <string_view>
#include <tuple>
#include <type_traits>
#include <typeindex>
#include <typeinfo>
#include <utility>
#include <valarray>
#include <variant>

// Streams
#include <fstream>
#include <iomanip>
#include <ios>
#include <iosfwd>
#include <iostream>
#include <istream>
#include <ostream>
#include <sstream>
#include <streambuf>

// Concurrency
#include <atomic>
#include <condition_variable>
#include <future>
#include <mutex>
#include <thread>

// C++20 additions, where the toolchain has them.
#if __cplusplus >= 202002L
#  if __has_include(<concepts>)
#    include <concepts>
#  endif
#  if __has_include(<numbers>)
#    include <numbers>
#  endif
#  if __has_include(<span>)
#    include <span>
#  endif
#  if __has_include(<ranges>)
#    include <ranges>
#  endif
#  if __has_include(<compare>)
#    include <compare>
#  endif
#endif
