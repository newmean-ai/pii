// Private fixtures and model copies, read in place: PII_FIXTURES (default js/test/fixtures) and PII_MODELS (default
// js/test/models, folders pii-<size> in the Hugging Face layout). They are made from validation text and are not
// published; the tests that need them skip without them.
import Foundation
import XCTest

enum Fixtures {
  static let root = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
    .deletingLastPathComponent().deletingLastPathComponent()
  static let env = ProcessInfo.processInfo.environment
  static let fixtures = URL(fileURLWithPath: env["PII_FIXTURES"] ?? root.appendingPathComponent("js/test/fixtures").path)
  static let models = URL(fileURLWithPath: env["PII_MODELS"] ?? root.appendingPathComponent("js/test/models").path)
  /// PII_SIZES=nano,medium limits the sizes.
  static let sizes: Set<String>? = env["PII_SIZES"].map { Set($0.split(separator: ",").map(String.init)) }

  static func modelDirectory(_ size: String) -> URL { models.appendingPathComponent("pii-\(size)") }

  static func rows(_ name: String) throws -> [[String: Any]] {
    let url = fixtures.appendingPathComponent(name)
    guard FileManager.default.fileExists(atPath: url.path) else { throw XCTSkip("no fixture \(name)") }
    return try String(contentsOf: url, encoding: .utf8).split(separator: "\n").map {
      try JSONSerialization.jsonObject(with: Data($0.utf8)) as! [String: Any]
    }
  }

  static func requireModel(_ size: String, _ variant: String) throws -> URL {
    let dir = modelDirectory(size)
    guard FileManager.default.fileExists(atPath: dir.appendingPathComponent("onnx/model_\(variant).onnx").path) else {
      throw XCTSkip("no model copy at \(dir.path)")
    }
    return dir
  }
}
