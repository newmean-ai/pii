// Build hook: compiles the native core (native/core in the repository, or the copy made by tool/vendor_core.dart for
// pub.dev) into one shared library per target (Android ABIs, iOS, macOS, Linux, Windows).
import 'dart:io';

import 'package:code_assets/code_assets.dart';
import 'package:hooks/hooks.dart';
import 'package:logging/logging.dart';
import 'package:native_toolchain_c/native_toolchain_c.dart';

const _sources = ['api.c', 'engine.c', 'onnx.c', 'tokenizer.c', 'util.c', 'sha256.c', 'models.c'];
const _headers = ['pii.h', 'src/internal.h', 'src/simd.h', 'src/unicode_tables.h'];

/// The core sources, relative to the package root: the repository checkout first, then the vendored copy.
String _coreDir(Uri packageRoot) {
  for (final dir in ['../native/core', 'third_party/native_core']) {
    if (File.fromUri(packageRoot.resolve('$dir/src/api.c')).existsSync() &&
        File.fromUri(packageRoot.resolve('$dir/pii.h')).existsSync()) {
      return dir;
    }
  }
  throw StateError(
    'newmean_pii: native core sources not found (../native/core or third_party/native_core); '
    'in the repository run `dart run tool/vendor_core.dart` or build from a checkout',
  );
}

void main(List<String> args) async {
  await build(args, (input, output) async {
    if (!input.config.buildCodeAssets) return;
    final code = input.config.code;
    final core = _coreDir(input.packageRoot);
    final msvc = code.targetOS == OS.windows;
    await CBuilder.library(
      name: 'newmean_pii',
      assetName: 'src/ffi.dart',
      sources: [for (final f in _sources) '$core/src/$f'],
      includes: [core],
      std: 'c11',
      // Same results as the npm package's baseline WebAssembly build: never fuse multiply and add. 32-bit ARM uses the
      // scalar path, since 32-bit NEON flushes denormals to zero.
      flags: msvc ? ['/fp:precise'] : ['-ffp-contract=off', '-fno-math-errno', '-fvisibility=hidden'],
      defines: {
        if (msvc) 'PII_BUILD_SHARED': null,
        if (code.targetArchitecture == Architecture.arm) 'PII_SCALAR': null,
      },
      optimizationLevel: OptimizationLevel.o3,
    ).run(
      input: input,
      output: output,
      logger: Logger('')
        ..level = Level.WARNING
        ..onRecord.listen((r) => stderr.writeln(r.message)),
    );
    output.dependencies.addAll([for (final h in _headers) input.packageRoot.resolve('$core/$h')]);
  });
}
