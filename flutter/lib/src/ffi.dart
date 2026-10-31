// Bindings to the native core (native/core/pii.h), written by hand: only the calls this package uses. The library is
// built by hook/build.dart and bound to this file's asset id.
@DefaultAsset('package:newmean_pii/src/ffi.dart')
library;

import 'dart:ffi';

import 'package:ffi/ffi.dart';

const piiOk = 0;
const piiUnitUtf16 = 2;
const piiSourceModel = 1;
const piiSourceRegex = 2;

final class PiiModel extends Opaque {}

final class PiiSpanC extends Struct {
  @Size()
  external int byteStart, byteEnd, cpStart, cpEnd, u16Start, u16End;
  @Int()
  external int label;
  external Pointer<Utf8> labelName;
  @Int()
  external int source;
}

final class PiiExtraSpanC extends Struct {
  @Size()
  external int start, end;
  @Int()
  external int label;
}

final class PiiOptionsC extends Struct {
  @Size()
  external int structSize;
  @Double()
  external double sensitivity;
  @Uint32()
  external int labels;
  external Pointer<PiiExtraSpanC> extra;
  @Size()
  external int nExtra;
  @Int()
  external int extraUnit;
}

final class PiiFilePinC extends Struct {
  external Pointer<Utf8> size, file, path;
  @Uint64()
  external int bytes;
  external Pointer<Utf8> sha256;
}

@Native<Pointer<Utf8> Function()>(symbol: 'pii_version', isLeaf: true)
external Pointer<Utf8> piiVersion();

@Native<Pointer<Utf8> Function()>(symbol: 'pii_build_info', isLeaf: true)
external Pointer<Utf8> piiBuildInfo();

@Native<Pointer<Utf8> Function(Int)>(symbol: 'pii_strerror', isLeaf: true)
external Pointer<Utf8> piiStrerror(int code);

@Native<Void Function(Pointer<PiiOptionsC>)>(symbol: 'pii_options_init', isLeaf: true)
external void piiOptionsInit(Pointer<PiiOptionsC> options);

// Not leaf calls: they can take a while, and a leaf call would hold up garbage collection in the other isolates.
@Native<Pointer<PiiModel> Function(Pointer<Uint8>, Size, Pointer<Uint8>, Size, Pointer<Char>, Size)>(
  symbol: 'pii_model_load',
)
external Pointer<PiiModel> piiModelLoad(
  Pointer<Uint8> onnx,
  int onnxLen,
  Pointer<Uint8> tokenizer,
  int tokenizerLen,
  Pointer<Char> err,
  int errLen,
);

@Native<Void Function(Pointer<PiiModel>)>(symbol: 'pii_model_free')
external void piiModelFree(Pointer<PiiModel> model);

@Native<
  Int Function(Pointer<PiiModel>, Pointer<Uint8>, Size, Pointer<PiiOptionsC>, Pointer<Pointer<PiiSpanC>>, Pointer<Size>)
>(symbol: 'pii_detect')
external int piiDetect(
  Pointer<PiiModel> model,
  Pointer<Uint8> utf8,
  int len,
  Pointer<PiiOptionsC> options,
  Pointer<Pointer<PiiSpanC>> out,
  Pointer<Size> nOut,
);

@Native<Void Function(Pointer<PiiSpanC>)>(symbol: 'pii_spans_free', isLeaf: true)
external void piiSpansFree(Pointer<PiiSpanC> spans);

@Native<Pointer<PiiFilePinC> Function(Pointer<Utf8>, Pointer<Utf8>)>(symbol: 'pii_model_file', isLeaf: true)
external Pointer<PiiFilePinC> piiModelFile(Pointer<Utf8> size, Pointer<Utf8> file);

/// Address of pii_model_free, for a [NativeFinalizer].
Pointer<NativeFinalizerFunction> get piiModelFreePointer =>
    Native.addressOf<NativeFunction<Void Function(Pointer<PiiModel>)>>(piiModelFree).cast();
