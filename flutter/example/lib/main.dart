// Type text and mask its personal information on the device. The model (nano by default here, 3.4 MB with the tokenizer)
// is downloaded once from Hugging Face and checked against its pinned SHA-256.
import 'package:flutter/material.dart';
import 'package:newmean_pii/newmean_pii.dart';

void main() => runApp(const MaterialApp(title: 'newmean-pii', home: RedactPage()));

class RedactPage extends StatefulWidget {
  const RedactPage({super.key});

  @override
  State<RedactPage> createState() => _RedactPageState();
}

class _RedactPageState extends State<RedactPage> {
  final _input = TextEditingController(
    text: '제 메일은 kim@example.com, 전화는 010-1234-5678입니다. Call Anna Müller tomorrow 🙂',
  );
  String _size = 'nano';
  RedactMode _mode = RedactMode.tag;
  NewmeanPii? _pii;
  String _status = '';
  PiiRedaction? _result;
  String _source = ''; // the text _result was made from (span offsets point into it, not into the edited field)
  bool _busy = false;

  Future<NewmeanPii> _model() async {
    final current = _pii;
    if (current != null && current.size == _size) return current;
    await current?.dispose();
    _pii = null;
    final pii = await NewmeanPii.create(
      size: _size,
      onProgress: (p) =>
          setState(() => _status = '${p.file}: ${p.cached ? 'cached' : '${p.loaded * 100 ~/ p.total}%'}'),
    );
    return _pii = pii;
  }

  Future<void> _run() async {
    setState(() => _busy = true);
    try {
      final pii = await _model();
      final text = _input.text;
      final sw = Stopwatch()..start();
      final r = await pii.redact(text, mode: _mode);
      setState(() {
        _result = r;
        _source = text;
        _status = 'newmean-pii-${pii.size} ${pii.variant}: ${r.spans.length} spans, ${sw.elapsedMilliseconds} ms';
      });
    } catch (e) {
      setState(() => _status = 'Error: $e');
    } finally {
      setState(() => _busy = false);
    }
  }

  @override
  void dispose() {
    _pii?.dispose();
    _input.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final r = _result;
    return Scaffold(
      appBar: AppBar(title: const Text('newmean-pii')),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          TextField(
            controller: _input,
            maxLines: 5,
            decoration: const InputDecoration(border: OutlineInputBorder()),
          ),
          const SizedBox(height: 12),
          SegmentedButton<String>(
            segments: [for (final s in modelSizes) ButtonSegment(value: s, label: Text(s))],
            selected: {_size},
            onSelectionChanged: (s) => setState(() => _size = s.first),
          ),
          const SizedBox(height: 8),
          SegmentedButton<RedactMode>(
            segments: [for (final m in RedactMode.values) ButtonSegment(value: m, label: Text(m.name))],
            selected: {_mode},
            onSelectionChanged: (m) => setState(() => _mode = m.first),
          ),
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: _busy ? null : _run,
            icon: _busy
                ? const SizedBox.square(dimension: 16, child: CircularProgressIndicator(strokeWidth: 2))
                : const Icon(Icons.shield_outlined),
            label: const Text('Redact'),
          ),
          const SizedBox(height: 12),
          Text(_status, style: Theme.of(context).textTheme.bodySmall),
          if (r != null) ...[
            const SizedBox(height: 12),
            SelectableText(r.text, style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 8),
            for (final s in r.spans)
              Text(
                '${s.label}  [${s.start}, ${s.end})  ${_source.substring(s.start, s.end)}',
                style: Theme.of(context).textTheme.bodySmall,
              ),
          ],
        ],
      ),
    );
  }
}
