// Model files: the pinned size and SHA-256 of every published file (from the C core, the same pins as the npm package),
// loading from a folder (app bundle) and a one-time download from Hugging Face into Caches. Every file is checked
// against its pin before use.
import CNewmeanPII
import CryptoKit
import Foundation

/// A published model file of release `PIIModelFile.version`.
public struct PIIModelFile: Sendable, Hashable {
  /// Model release (Hugging Face tag v1.0.0).
  public static let version = PII_MODEL_VERSION

  public let size: PIISize
  /// Path inside the model folder: "onnx/model_q4e.onnx" or "tokenizer.web.json".
  public let path: String
  public let bytes: Int
  /// Lowercase hex.
  public let sha256: String

  /// The weight file of a size and variant.
  public static func model(_ size: PIISize, _ variant: PIIVariant) -> PIIModelFile { pin(size, variant.rawValue) }
  /// The tokenizer (the same file for every size).
  public static func tokenizer(_ size: PIISize) -> PIIModelFile { pin(size, "tokenizer") }

  private static func pin(_ size: PIISize, _ file: String) -> PIIModelFile {
    let p = pii_model_file(size.rawValue, file)!.pointee
    return PIIModelFile(size: size, path: String(cString: p.path), bytes: Int(p.bytes), sha256: String(cString: p.sha256))
  }

  /// Hugging Face folder of a size at the pinned release: https://huggingface.co/newmean-ai/pii-<size>/resolve/v1.0.0/
  public static func defaultBaseURL(_ size: PIISize) -> URL {
    URL(string: "https://huggingface.co/newmean-ai/pii-\(size.rawValue)/resolve/v\(version)/")!
  }

  /// Reads the file from a model folder (Hugging Face layout, or flat as in an app bundle) and checks it.
  func read(from directory: URL) throws -> Data {
    let name = (path as NSString).lastPathComponent
    let fm = FileManager.default
    guard let url = [directory.appendingPathComponent(path), directory.appendingPathComponent(name)].first(where: { fm.fileExists(atPath: $0.path) })
    else { throw PIIError.fileNotFound(directory.appendingPathComponent(path)) }
    let data = try Data(contentsOf: url, options: .alwaysMapped)
    try verify(data)
    return data
  }

  /// Throws unless data has the published size and SHA-256.
  public func verify(_ data: Data) throws {
    let name = "pii-\(size.rawValue)/\(path)"
    guard data.count == bytes else { throw PIIError.verificationFailed(file: name, reason: "\(data.count) bytes, expected \(bytes)") }
    let hex = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    guard hex == sha256 else { throw PIIError.verificationFailed(file: name, reason: "SHA-256 \(hex)") }
  }
}

/// Downloads model files once and keeps them in a folder (default: Caches/newmean-pii/v<version>/pii-<size>/).
public struct PIIModelStore: Sendable {
  public let directory: URL

  /// The default store in the user's Caches folder (the system may clear it; files are downloaded again then).
  public static var `default`: PIIModelStore {
    let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
    return PIIModelStore(directory: caches.appendingPathComponent("newmean-pii", isDirectory: true))
  }

  public init(directory: URL) {
    self.directory = directory
  }

  /// Folder of a size inside the store (Hugging Face layout); pass it as `modelDirectory`.
  public func modelDirectory(_ size: PIISize) -> URL {
    directory.appendingPathComponent("v\(PIIModelFile.version)/pii-\(size.rawValue)", isDirectory: true)
  }

  /// Makes sure the tokenizer and the weight file of size and variant are in the store and match their pins,
  /// downloading missing or damaged files from `base` (default: Hugging Face). Returns `modelDirectory(size)`.
  @discardableResult
  public func download(_ size: PIISize = .default, variant: PIIVariant? = nil, from base: URL? = nil,
                       session: URLSession = .shared) async throws -> URL {
    let dir = modelDirectory(size)
    for file in [PIIModelFile.tokenizer(size), .model(size, variant ?? size.defaultVariant)] {
      try await fetch(file, into: dir, from: base ?? PIIModelFile.defaultBaseURL(size), session: session)
    }
    return dir
  }

  private func fetch(_ file: PIIModelFile, into dir: URL, from base: URL, session: URLSession) async throws {
    let fm = FileManager.default
    let dest = dir.appendingPathComponent(file.path)
    if fm.fileExists(atPath: dest.path) {
      if (try? file.verify(Data(contentsOf: dest, options: .alwaysMapped))) != nil { return }
      try? fm.removeItem(at: dest)
    }
    let url = base.appendingPathComponent(file.path)
    let (tmp, response) = try await session.download(from: url)
    defer { try? fm.removeItem(at: tmp) }
    let status = (response as? HTTPURLResponse)?.statusCode ?? 200
    guard status == 200 else { throw PIIError.downloadFailed(url, status: status) }
    try file.verify(Data(contentsOf: tmp, options: .alwaysMapped))
    try fm.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
    do {
      try fm.moveItem(at: tmp, to: dest)
    } catch {
      // another load may have stored the file meanwhile
      try file.verify(Data(contentsOf: dest, options: .alwaysMapped))
    }
  }
}
