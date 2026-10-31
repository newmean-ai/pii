// swift-tools-version:5.9
// Swift package NewmeanPII (iOS, macOS): the newmean-pii-<size> models on device, over the shared C core in native/core.
// Sources: swift/ (wrapper, tests, README), native/core (engine, tokenizer, decoding). See swift/README.md.
import PackageDescription

let package = Package(
  name: "NewmeanPII",
  platforms: [.iOS(.v15), .macOS(.v12)],
  products: [
    .library(name: "NewmeanPII", targets: ["NewmeanPII"])
  ],
  targets: [
    // The C core in native/core, compiled through one-line shims (swift/Sources/CNewmeanPII/*.c include
    // native/core/src/*.c) and imported through a module map that exposes pii.h only. src/simd.h carries
    // `#pragma STDC FP_CONTRACT OFF`, so Clang keeps every multiply and add separate (results bit-identical to the npm
    // package's baseline WebAssembly build) without extra flags.
    .target(
      name: "CNewmeanPII",
      path: "swift/Sources/CNewmeanPII"
    ),
    .target(
      name: "NewmeanPII",
      dependencies: ["CNewmeanPII"],
      path: "swift/Sources/NewmeanPII"
    ),
    .testTarget(
      name: "NewmeanPIITests",
      dependencies: ["NewmeanPII"],
      path: "swift/Tests/NewmeanPIITests"
    ),
  ]
)
