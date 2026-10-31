// Copies the native core (../native/core: pii.h and src/) into third_party/native_core for `dart pub publish`; the
// build hook uses it when ../native/core is not there (the package as installed from pub.dev). The copy is ignored by
// git (.gitignore) but published (.pubignore). `--clean` removes it.
//
//   dart run tool/vendor_core.dart && dart pub publish --dry-run && dart run tool/vendor_core.dart --clean
import 'dart:io';

void main(List<String> args) {
  final root = File.fromUri(Platform.script).parent.parent;
  final src = Directory('${root.path}/../native/core');
  final dst = Directory('${root.path}/third_party/native_core');
  if (dst.existsSync()) dst.deleteSync(recursive: true);
  if (args.contains('--clean')) {
    final parent = dst.parent;
    if (parent.existsSync() && parent.listSync().isEmpty) parent.deleteSync();
    print('removed ${dst.path}');
    return;
  }
  if (!File('${src.path}/pii.h').existsSync()) {
    stderr.writeln('native core not found at ${src.path}');
    exit(1);
  }
  final files = [
    'pii.h',
    'LICENSE',
    for (final f in Directory('${src.path}/src').listSync().whereType<File>())
      if (f.path.endsWith('.c') || f.path.endsWith('.h')) 'src/${f.uri.pathSegments.last}',
  ]..sort();
  for (final f in files) {
    final out = File('${dst.path}/$f')..parent.createSync(recursive: true);
    File('${src.path}/$f').copySync(out.path);
  }
  print('copied ${files.length} files to ${dst.path}');
}
