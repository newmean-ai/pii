// Offsets of the C core (UTF-8 bytes) -> String.Index, in one pass over the text whatever the order of the offsets.
extension String {
  /// Indices of UTF-8 offsets on scalar boundaries. O(n + k log k); O(1) per step on native (UTF-8) strings.
  func indices(utf8Offsets offsets: [Int]) -> [String.Index] {
    let view = utf8
    var out = [String.Index](repeating: view.startIndex, count: offsets.count)
    var at = view.startIndex
    var pos = 0
    for k in offsets.indices.sorted(by: { offsets[$0] < offsets[$1] }) {
      at = view.index(at, offsetBy: offsets[k] - pos)
      pos = offsets[k]
      out[k] = at
    }
    return out
  }

  /// Calls body with the text as contiguous UTF-8 (no copy for native strings).
  func withContiguousUTF8<R>(_ body: (UnsafeBufferPointer<UInt8>) throws -> R) rethrows -> R {
    var s = self
    return try s.withUTF8(body)
  }
}
