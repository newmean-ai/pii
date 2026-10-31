// The package layout: every C file of native/core/src is compiled through a shim in swift/Sources/CNewmeanPII, and the
// build reports its kernel.
import NewmeanPII
import XCTest

final class PackageTests: XCTestCase {
  func testEveryCoreFileHasAShim() throws {
    let fm = FileManager.default
    let core = Fixtures.root.appendingPathComponent("native/core/src")
    let shims = Fixtures.root.appendingPathComponent("swift/Sources/CNewmeanPII")
    let sources = try fm.contentsOfDirectory(atPath: core.path).filter { $0.hasSuffix(".c") }.sorted()
    XCTAssertFalse(sources.isEmpty)
    XCTAssertEqual(try fm.contentsOfDirectory(atPath: shims.path).filter { $0.hasSuffix(".c") }.sorted(), sources)
    for name in sources {
      let shim = try String(contentsOf: shims.appendingPathComponent(name), encoding: .utf8)
      XCTAssertTrue(shim.contains("#include \"../../../native/core/src/\(name)\""), name)
    }
  }

  func testBuildInfo() {
    // the default build: no fused multiply-add, results bit-identical to the npm package's baseline WebAssembly build
    let info = PIIRedactor.buildInfo
    XCTAssertFalse(info.contains("fma"), info)
    XCTAssertTrue(["neon", "sse", "vector", "scalar"].contains(info), info)
  }
}
