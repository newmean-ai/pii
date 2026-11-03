// User lists (options allow, block and listsId, added in 1.4.0): checked here in the order of the npm package
// (js/src/lists.ts), so that no encoding error carries an entry's text, then handed to pii_lists_create, which repeats the
// checks and adds the ones across entries. Rules: pii_lists_create in native/core/pii.h.
import 'dart:convert';
import 'dart:ffi';

import 'package:ffi/ffi.dart';

import 'ffi.dart';
import 'span.dart';

const _maxEntries = 10000;
const _maxCp = 64;
const _maxU16 = 256;
const _maxId = 128;

/// A value you know is not PII in your data (a region, a brand, a job): a span whose whole text is [text] is not masked.
/// With [label], only spans of that label family (PERSON covers PERSON_L2, URL covers URL_L2).
final class PiiAllowEntry {
  final String text;

  /// One of [piiLabels], or null for any label.
  final String? label;

  const PiiAllowEntry(this.text, {this.label});

  @override
  bool operator ==(Object other) => other is PiiAllowEntry && other.text == text && other.label == label;

  @override
  int get hashCode => Object.hash(text, label);
}

/// A value you know is PII (an instructor's name): every occurrence on word boundaries is masked with [label].
final class PiiBlockEntry {
  final String text;

  /// One of [piiLabels].
  final String label;

  const PiiBlockEntry(this.text, this.label);

  @override
  bool operator ==(Object other) => other is PiiBlockEntry && other.text == text && other.label == label;

  @override
  int get hashCode => Object.hash(text, label);
}

/// An invalid `allow`, `block` or `listsId` option of `NewmeanPii.create` / `fromBytes`. Carries where and why, never the
/// entry's text.
final class PiiListException implements Exception {
  /// "allow", "block" or "meta" (`listsId`).
  final String list;

  /// Entry index in [list]; null for `listsId`.
  final int? index;

  /// "bad_shape", "too_long", "bad_text", "empty", "edge_space", "unknown_label", "too_many", "label_conflict", "in_both"
  /// or "bad_lists_id" (the same reasons as the npm package's ListError).
  final String reason;

  const PiiListException(this.list, this.index, this.reason);

  @override
  String toString() =>
      'PiiListException: ${list == 'meta' ? 'listsId' : '$list list'}${index == null ? '' : ' entry $index'}: $reason';
}

/// Entries of the lists after merging duplicates, and the caller's listsId ([NewmeanPii.info]).
final class PiiListsInfo {
  final int allow, block;

  /// The `listsId` given to `create`, or null.
  final String? id;

  const PiiListsInfo(this.allow, this.block, this.id);

  @override
  String toString() => 'PiiListsInfo(allow $allow, block $block, id $id)';
}

/// The 25 White_Space code points of Unicode 17.0.
bool _whiteSpace(int c) =>
    (c >= 0x09 && c <= 0x0d) ||
    c == 0x20 ||
    c == 0x85 ||
    c == 0xa0 ||
    c == 0x1680 ||
    (c >= 0x2000 && c <= 0x200a) ||
    c == 0x2028 ||
    c == 0x2029 ||
    c == 0x202f ||
    c == 0x205f ||
    c == 0x3000;

bool _lineBreak(int c) => c == 0x0a || c == 0x0d || c == 0x85 || c == 0x2028 || c == 0x2029;

/// Code points of [s], or null when it holds an unpaired surrogate.
List<int>? _codePoints(String s) {
  final out = <int>[];
  for (var i = 0; i < s.length; i++) {
    final c = s.codeUnitAt(i);
    if (c >= 0xd800 && c <= 0xdbff) {
      final d = i + 1 < s.length ? s.codeUnitAt(i + 1) : 0;
      if (d < 0xdc00 || d > 0xdfff) return null;
      out.add(((c - 0xd800) << 10) + (d - 0xdc00) + 0x10000);
      i++;
    } else if (c >= 0xdc00 && c <= 0xdfff) {
      return null;
    } else {
      out.add(c);
    }
  }
  return out;
}

/// Checks 2-6 of Section 3 on a text; the failing reason, or null.
String? _checkText(String text) {
  if (text.length > _maxU16) return 'too_long'; // 256 UTF-16 units also bound the UTF-8 bytes (at most 768)
  final cps = _codePoints(text);
  if (cps == null || cps.any((c) => c == 0 || _lineBreak(c))) return 'bad_text';
  if (cps.isEmpty) return 'empty';
  if (cps.length > _maxCp) return 'too_long';
  if (_whiteSpace(cps.first) || _whiteSpace(cps.last)) return 'edge_space';
  return null;
}

/// Validates listsId (checked first, before the lists): 1 to 128 code points, no U+0000, unpaired surrogate or line
/// break.
String? checkListsId(String? id) {
  if (id == null) return null;
  final cps = _codePoints(id);
  if (cps == null || cps.isEmpty || cps.length > _maxId || cps.any((c) => c == 0 || _lineBreak(c))) {
    throw const PiiListException('meta', null, 'bad_lists_id');
  }
  return id;
}

/// (text, label id or -1) of each entry; at most 10,000 are read.
List<(String, int)> _readList<T>(Iterable<T>? list, String name, (String, String?) Function(T) entry) {
  final out = <(String, int)>[];
  if (list == null) return out;
  var i = 0;
  for (final x in list) {
    if (i == _maxEntries) throw PiiListException(name, i, 'too_many');
    final (text, label) = entry(x);
    final reason = _checkText(text);
    if (reason != null) throw PiiListException(name, i, reason);
    final id = label == null ? -1 : piiLabels.indexOf(label);
    if (label != null && id < 0) throw PiiListException(name, i, 'unknown_label');
    out.add((text, id));
    i++;
  }
  return out;
}

const _reasons = {
  1: 'bad_shape',
  2: 'too_long',
  3: 'bad_text',
  4: 'empty',
  5: 'edge_space',
  6: 'unknown_label',
  7: 'too_many',
  8: 'label_conflict',
  9: 'in_both',
};

/// Validates the lists (allow, then block, then the checks across entries in the native core) and returns
/// (pii_lists address, allow count, block count) after merging; address 0 when both lists are empty. Throws
/// [PiiListException].
(int, int, int) createLists(Iterable<PiiAllowEntry>? allow, Iterable<PiiBlockEntry>? block) {
  final a = _readList(allow, 'allow', (PiiAllowEntry e) => (e.text, e.label));
  final b = _readList(block, 'block', (PiiBlockEntry e) => (e.text, e.label));
  if (a.isEmpty && b.isEmpty) return (0, 0, 0);
  return using((arena) {
    Pointer<PiiTermC> terms(List<(String, int)> list) {
      if (list.isEmpty) return nullptr;
      final p = arena<PiiTermC>(list.length);
      for (var i = 0; i < list.length; i++) {
        final bytes = utf8.encode(list[i].$1); // well-formed: checked above
        final u = arena<Uint8>(bytes.length);
        u.asTypedList(bytes.length).setAll(0, bytes);
        p[i]
          ..utf8 = u.cast()
          ..len = bytes.length
          ..label = list[i].$2;
      }
      return p;
    }

    final out = arena<Pointer<PiiLists>>();
    final err = arena<PiiListErrorC>()..ref.reason = 0;
    final rc = piiListsCreate(terms(a), a.length, terms(b), b.length, out, err);
    if (rc != piiOk) {
      final e = err.ref, reason = _reasons[e.reason];
      if (reason == null) throw StateError('newmean_pii: lists failed (code $rc)');
      throw PiiListException(e.list == piiListAllow ? 'allow' : 'block', e.index, reason);
    }
    final nAllow = arena<Size>(), nBlock = arena<Size>();
    piiListsCounts(out.value, nAllow, nBlock);
    return (out.value.address, nAllow.value, nBlock.value);
  });
}

void freeLists(int lists) {
  if (lists != 0) piiListsFree(Pointer<PiiLists>.fromAddress(lists));
}
