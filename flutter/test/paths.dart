// Private test data, read in place (never copied): PII_FIXTURES (default ../js/test/fixtures) and PII_MODELS (default
// ../js/test/models, the four Hugging Face model repos as pii-<size>/). Tests that need them skip when they are absent.
import 'dart:convert';
import 'dart:io';

final String fixtures = Platform.environment['PII_FIXTURES'] ?? '../js/test/fixtures';
final String models = Platform.environment['PII_MODELS'] ?? '../js/test/models';

/// PII_SIZES=nano,medium limits the sizes of the model tests.
final List<String>? sizes = Platform.environment['PII_SIZES']?.split(',');

String modelDir(String size) => '$models/pii-$size';

bool hasModel(String size, String variant) => File('${modelDir(size)}/onnx/model_$variant.onnx').existsSync();

List<Map<String, dynamic>> readJsonl(String path) => [
  for (final l in LineSplitter.split(File(path).readAsStringSync()))
    if (l.trim().isNotEmpty) jsonDecode(l) as Map<String, dynamic>,
];

/// UTF-16 offset of each code point index of [text] (fixture spans are in Python code points).
List<int> codePointToUtf16(String text) {
  final units = [0];
  for (final r in text.runes) {
    units.add(units.last + (r > 0xFFFF ? 2 : 1));
  }
  return units;
}
