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
    // the default build: fused multiply-add on arm64 (results equal to the npm package's relaxed-SIMD WebAssembly build);
    // on x86-64 chosen once per process, "avx2" where the CPU has AVX2, else "sse" (both bit-identical to its baseline
    // build), and the fused "avx2+fma" only on request (NEWMEAN_PII_KERNEL)
    let info = PIIRedactor.buildInfo
    #if arch(arm64)
    XCTAssertEqual(info, "neon+fma")
    #elseif arch(x86_64)
    let optIn = ProcessInfo.processInfo.environment["NEWMEAN_PII_KERNEL"] != nil
    XCTAssertTrue(["avx2", "sse"].contains(info) || (optIn && info == "avx2+fma"), info)
    #else
    XCTAssertTrue(["vector", "scalar"].contains(info), info)
    #endif
  }
}
