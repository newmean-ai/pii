// Downloads from a local HTTP server that serves the nano model folder (no network), and, with PII_DOWNLOAD=1, once from
// Hugging Face.
import 'dart:io';

import 'package:newmean_pii/newmean_pii.dart';
import 'package:test/test.dart';

import 'paths.dart';

void main() {
  // the local server serves the nano model folder; the Hugging Face test does not need it
  final skip = hasModel('nano', 'q4') ? false : 'no nano model at ${modelDir('nano')}';
  late HttpServer server;
  late Directory cache;
  final requests = <String>[];
  var tamper = false;

  setUp(() async {
    cache = await Directory.systemTemp.createTemp('newmean_pii_test');
    requests.clear();
    tamper = false;
    server = await HttpServer.bind(InternetAddress.loopbackIPv4, 0);
    server.listen((req) async {
      requests.add(req.uri.path);
      final f = File('${modelDir('nano')}${req.uri.path}');
      if (!f.existsSync()) {
        req.response.statusCode = 404;
      } else {
        final bytes = f.readAsBytesSync();
        if (tamper) bytes[bytes.length - 1] ^= 1;
        req.response.contentLength = bytes.length;
        req.response.add(bytes);
      }
      await req.response.close();
    });
  });
  tearDown(() async {
    await server.close(force: true);
    await cache.delete(recursive: true);
  });

  Uri base() => Uri.parse('http://127.0.0.1:${server.port}/');

  test('downloads once, verifies, and reuses the cache', () async {
    final progress = <PiiProgress>[];
    final a = await NewmeanPii.create(size: 'nano', modelUrl: base(), cacheDir: cache.path, onProgress: progress.add);
    expect(requests..sort(), ['/onnx/model_q4.onnx', '/tokenizer.web.json']);
    expect(progress.where((p) => !p.cached && p.loaded == p.total).map((p) => p.file).toSet(), {
      'tokenizer.web.json',
      'onnx/model_q4.onnx',
    });
    expect(File('${cache.path}/v1.0.0/pii-nano/onnx/model_q4.onnx').lengthSync(), modelFile('nano', 'q4').bytes);
    final spans = await a.detect('kim@example.com');
    await a.dispose();

    requests.clear();
    final b = await NewmeanPii.create(size: 'nano', modelUrl: base(), cacheDir: cache.path);
    expect(requests, isEmpty);
    expect(await b.detect('kim@example.com'), spans);
    await b.dispose();

    // a corrupted cached copy is replaced
    final cached = File('${cache.path}/v1.0.0/pii-nano/tokenizer.web.json');
    cached.writeAsStringSync('{}');
    final c = await NewmeanPii.create(size: 'nano', modelUrl: base(), cacheDir: cache.path);
    expect(requests, ['/tokenizer.web.json']);
    expect(cached.lengthSync(), modelFile('nano', 'tokenizer').bytes);
    await c.dispose();
  }, skip: skip);

  test('refuses a file that does not match the pin, and keeps nothing', () async {
    tamper = true;
    await expectLater(
      NewmeanPii.create(size: 'nano', modelUrl: base(), cacheDir: cache.path),
      throwsA(isA<StateError>().having((e) => e.message, 'message', contains('SHA-256'))),
    );
    expect(File('${cache.path}/v1.0.0/pii-nano/onnx/model_q4.onnx').existsSync(), isFalse);
  }, skip: skip);

  test('HTTP errors', () async {
    await expectLater(
      NewmeanPii.create(size: 'nano', modelUrl: base().resolve('missing/'), cacheDir: cache.path),
      throwsA(isA<HttpException>()),
    );
  }, skip: skip);

  test('modelDir with a wrong file is refused', () async {
    final dir = Directory('${cache.path}/m/onnx')..createSync(recursive: true);
    File('${modelDir('nano')}/tokenizer.web.json').copySync('${cache.path}/m/tokenizer.web.json');
    File('${modelDir('nano')}/onnx/model_q4e.onnx').copySync('${dir.path}/model_q4.onnx');
    await expectLater(NewmeanPii.create(size: 'nano', modelDir: '${cache.path}/m'), throwsStateError);
  }, skip: skip);

  test('downloads from Hugging Face (PII_DOWNLOAD=1)', () async {
    final a = await NewmeanPii.create(size: 'nano', cacheDir: cache.path);
    expect((await a.detect('Email kim@example.com')).map((s) => s.label), ['EMAIL']);
    await a.dispose();
  }, skip: Platform.environment['PII_DOWNLOAD'] == '1' ? false : 'set PII_DOWNLOAD=1 to download from huggingface.co');
}
