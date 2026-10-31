// Finds a model file (a local folder, the download cache, or Hugging Face), and checks its size and SHA-256 against the
// pin before use. Runs in a background isolate.
import 'dart:io';
import 'dart:isolate';
import 'dart:typed_data';

import 'package:crypto/crypto.dart' as crypto;

import 'models.dart';

/// Download progress of one file.
final class PiiProgress {
  /// `tokenizer.web.json` or `onnx/model_<variant>.onnx`.
  final String file;
  final int loaded, total;

  /// true when the file came from the cache or a local folder.
  final bool cached;
  const PiiProgress(this.file, this.loaded, this.total, this.cached);

  @override
  String toString() => 'PiiProgress($file, $loaded/$total${cached ? ', cached' : ''})';
}

/// Where downloads are kept by default: `~/Library/Caches/newmean-pii` (macOS, iOS), $XDG_CACHE_HOME or ~/.cache (Linux),
/// %LOCALAPPDATA% (Windows), the app's temporary directory elsewhere (Android).
String defaultCacheDir() {
  final env = Platform.environment;
  String? base;
  if (Platform.isMacOS || Platform.isIOS) {
    if (env['HOME'] != null) base = '${env['HOME']}/Library/Caches';
  } else if (Platform.isLinux) {
    base = env['XDG_CACHE_HOME'] ?? (env['HOME'] != null ? '${env['HOME']}/.cache' : null);
  } else if (Platform.isWindows) {
    base = env['LOCALAPPDATA'];
  }
  base ??= Directory.systemTemp.path;
  return '$base${Platform.pathSeparator}newmean-pii';
}

bool matchesPin(Uint8List bytes, PiiModelFile pin) =>
    bytes.length == pin.bytes && crypto.sha256.convert(bytes).toString() == pin.sha256;

Never _mismatch(String where, PiiModelFile pin) => throw StateError(
  'newmean_pii: $where does not match the published ${pin.path} of pii-${pin.size} '
  'v$modelVersion (size or SHA-256 differs)',
);

/// Reads [pin] from [modelDir], the cache under [cacheDir], or downloads it from [baseUrl] (default: Hugging Face).
Future<Uint8List> loadModelFile(
  PiiModelFile pin, {
  String? modelDir,
  Uri? baseUrl,
  String? cacheDir,
  bool cache = true,
  bool verify = true,
  SendPort? progress,
}) async {
  void report(int loaded, int total, bool cached) => progress?.send([pin.path, loaded, total, cached]);

  if (modelDir != null) {
    final f = File('$modelDir/${pin.path}');
    final bytes = await f.readAsBytes();
    if (verify && !matchesPin(bytes, pin)) _mismatch(f.path, pin);
    report(bytes.length, bytes.length, true);
    return bytes;
  }

  final cached = File(
    [
      cacheDir ?? defaultCacheDir(),
      'v$modelVersion',
      'pii-${pin.size}',
      ...pin.path.split('/'),
    ].join(Platform.pathSeparator),
  );
  if (cache && cached.existsSync()) {
    final bytes = await cached.readAsBytes();
    if (matchesPin(bytes, pin)) {
      report(bytes.length, bytes.length, true);
      return bytes;
    }
    await cached.delete(); // corrupt or stale copy: download again
  }

  final url = (baseUrl ?? Uri.parse('https://huggingface.co/newmean-ai/pii-${pin.size}/resolve/v$modelVersion/'))
      .resolve(pin.path);
  final client = HttpClient()..userAgent = 'newmean_pii-dart/$modelVersion';
  final Uint8List bytes;
  try {
    final res = await (await client.getUrl(url)).close();
    if (res.statusCode != 200) {
      await res.drain<void>();
      throw HttpException('newmean_pii: failed to download $url: HTTP ${res.statusCode}', uri: url);
    }
    final total = res.contentLength > 0 ? res.contentLength : pin.bytes;
    final builder = BytesBuilder(copy: false);
    await for (final chunk in res) {
      builder.add(chunk);
      if (verify && builder.length > pin.bytes) break; // longer than the pinned file: fails the check below
      report(builder.length, total, false);
    }
    bytes = builder.takeBytes();
  } finally {
    client.close(force: true);
  }
  if (verify && !matchesPin(bytes, pin)) _mismatch(url.toString(), pin);

  if (cache) {
    // unique per call: two isolates of one process may download the same file at once
    final tmp = File('${cached.path}.$pid.${DateTime.now().microsecondsSinceEpoch}.tmp');
    try {
      await cached.parent.create(recursive: true);
      await tmp.writeAsBytes(bytes, flush: true);
      await tmp.rename(cached.path);
    } on FileSystemException {
      // read-only or full disk: keep working without the cache
      try {
        if (tmp.existsSync()) tmp.deleteSync();
      } on FileSystemException {
        // nothing more to do
      }
    }
  }
  return bytes;
}
