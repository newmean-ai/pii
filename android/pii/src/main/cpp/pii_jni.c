// JNI bridge of ai.newmean.pii.internal.NativeBridge over the native core (native/core/pii.h).
// Text arrives as standard UTF-8 bytes (ai.newmean.pii.internal.Utf8), never through GetStringUTFChars: its modified UTF-8
// splits emoji into surrogates and NUL into two bytes, which changes tokens and offsets.
#include <jni.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "pii.h"

#define FN(name) Java_ai_newmean_pii_internal_NativeBridge_##name

// What this file and the Kotlin side assume about the core (pii_options and the lists API of native/core/pii.h). The
// structs come from pii.h and are filled by field name, so the compiler keeps their layout in step; these are the rest:
// handles travel as jlong, labels, sources and list errors as jint, and Kotlin decodes these codes by value
// (PiiLabels.ALL, PiiRedactor.sourceOf, PiiListReason, list 0 = allow).
_Static_assert(sizeof(jlong) >= sizeof(void *), "a model or lists handle must fit in a jlong");
_Static_assert(sizeof(jint) >= sizeof(int), "labels, sources and list errors must fit in a jint");
_Static_assert(sizeof(((pii_options *)0)->lists) == sizeof(void *), "pii_options.lists is a pointer (1.4.0 header)");
_Static_assert(PII_LABEL_COUNT == 13, "PiiLabels.ALL has 13 labels in model order");
_Static_assert(PII_SOURCE_MODEL == 1 && PII_SOURCE_REGEX == 2 && PII_SOURCE_TABLE == 4 && PII_SOURCE_BLOCK == 8,
               "PiiRedactor.sourceOf maps these bit patterns");
_Static_assert(PII_LIST_ALLOW == 0 && PII_LIST_BLOCK == 1, "PreparedLists.create reads list 0 as allow");
_Static_assert(PII_LIST_ERR_BAD_SHAPE == 1 && PII_LIST_ERR_TOO_LONG == 2 && PII_LIST_ERR_BAD_TEXT == 3 && PII_LIST_ERR_EMPTY == 4 &&
                   PII_LIST_ERR_EDGE_SPACE == 5 && PII_LIST_ERR_UNKNOWN_LABEL == 6 && PII_LIST_ERR_TOO_MANY == 7 &&
                   PII_LIST_ERR_LABEL_CONFLICT == 8 && PII_LIST_ERR_IN_BOTH == 9,
               "PiiListReason.code values");

static void throw_new(JNIEnv *env, const char *cls, const char *msg) {
  if ((*env)->ExceptionCheck(env)) return;
  jclass c = (*env)->FindClass(env, cls);
  if (c) (*env)->ThrowNew(env, c, msg);
}

static void throw_code(JNIEnv *env, int code) {
  const char *cls = code == PII_ERR_NOMEM ? "java/lang/OutOfMemoryError"
                    : code == PII_ERR_ARG ? "java/lang/IllegalArgumentException"
                                          : "java/lang/IllegalStateException";
  throw_new(env, cls, pii_strerror(code));
}

static const pii_model *model_of(JNIEnv *env, jlong handle) {
  if (!handle) throw_new(env, "java/lang/IllegalStateException", "model is closed");
  return (const pii_model *)(intptr_t)handle;
}

JNIEXPORT jlong JNICALL FN(modelLoad)(JNIEnv *env, jclass cls, jbyteArray onnx, jbyteArray tokenizer) {
  (void)cls;
  jsize n_onnx = (*env)->GetArrayLength(env, onnx), n_tok = (*env)->GetArrayLength(env, tokenizer);
  jbyte *p_onnx = (*env)->GetByteArrayElements(env, onnx, NULL);
  if (!p_onnx) return 0;
  jbyte *p_tok = (*env)->GetByteArrayElements(env, tokenizer, NULL);
  if (!p_tok) {
    (*env)->ReleaseByteArrayElements(env, onnx, p_onnx, JNI_ABORT);
    return 0;
  }
  char err[256] = "";
  pii_model *m = pii_model_load((const uint8_t *)p_onnx, (size_t)n_onnx, (const uint8_t *)p_tok, (size_t)n_tok, err, sizeof err);
  (*env)->ReleaseByteArrayElements(env, tokenizer, p_tok, JNI_ABORT);
  (*env)->ReleaseByteArrayElements(env, onnx, p_onnx, JNI_ABORT);
  if (!m) throw_new(env, "java/lang/IllegalArgumentException", err[0] ? err : "the model files could not be loaded");
  return (jlong)(intptr_t)m;
}

JNIEXPORT void JNICALL FN(modelFree)(JNIEnv *env, jclass cls, jlong handle) {
  (void)env, (void)cls;
  pii_model_free((pii_model *)(intptr_t)handle);
}

// [hidden, layers, layer_applications, vocab, max_tokens, tags, embedding_kind]
JNIEXPORT jintArray JNICALL FN(modelInfo)(JNIEnv *env, jclass cls, jlong handle) {
  (void)cls;
  const pii_model *m = model_of(env, handle);
  if (!m) return NULL;
  pii_model_info i;
  pii_model_get_info(m, &i);
  jint v[7] = {i.hidden, i.layers, i.layer_applications, i.vocab, i.max_tokens, i.tags, i.embedding_kind};
  jintArray out = (*env)->NewIntArray(env, 7);
  if (out) (*env)->SetIntArrayRegion(env, out, 0, 7, v);
  return out;
}

// pii_detect (table NULL) or pii_detect_table; spans as [utf16_start, utf16_end, label, source] * n. extra: [utf16_start,
// utf16_end, label] * n (pattern spans) or null.
// lists: handle of listsCreate or 0 (borrowed for the call).
static jintArray run_detect(JNIEnv *env, jlong handle, jbyteArray text, jdouble sensitivity, jint labels, jintArray extra,
                            jboolean keep_emoji, jboolean whole_words, jboolean strict_short_names, jlong lists,
                            const pii_table_options *table) {
  const pii_model *m = model_of(env, handle);
  if (!m) return NULL;
  pii_options o;
  pii_options_init(&o);
  o.lists = (const pii_lists *)(intptr_t)lists;
  o.sensitivity = sensitivity;
  o.labels = (uint32_t)labels;
  o.keep_emoji = keep_emoji ? 1 : 0;
  o.whole_words = whole_words ? 1 : 0;
  o.strict_short_names = strict_short_names ? 1 : 0;
  pii_extra_span *ex = NULL;
  jsize n_extra = extra ? (*env)->GetArrayLength(env, extra) / 3 : 0;
  if (n_extra > 0) {
    jint *e = (*env)->GetIntArrayElements(env, extra, NULL);
    if (!e) return NULL;
    ex = malloc((size_t)n_extra * sizeof *ex);
    if (ex)
      for (jsize i = 0; i < n_extra; i++) {
        ex[i].start = (size_t)(uint32_t)e[3 * i];
        ex[i].end = (size_t)(uint32_t)e[3 * i + 1];
        ex[i].label = e[3 * i + 2];
      }
    (*env)->ReleaseIntArrayElements(env, extra, e, JNI_ABORT);
    if (!ex) {
      throw_code(env, PII_ERR_NOMEM);
      return NULL;
    }
    o.extra = ex;
    o.n_extra = (size_t)n_extra;
    o.extra_unit = PII_UNIT_UTF16;
  }
  jsize len = (*env)->GetArrayLength(env, text);
  jbyte *t = len ? (*env)->GetByteArrayElements(env, text, NULL) : NULL;
  if (len && !t) {
    free(ex);
    return NULL;
  }
  pii_span *spans = NULL;
  size_t n = 0;
  const char *u = t ? (const char *)t : "";
  int rc = table ? pii_detect_table(m, u, (size_t)len, &o, table, &spans, &n) : pii_detect(m, u, (size_t)len, &o, &spans, &n);
  if (t) (*env)->ReleaseByteArrayElements(env, text, t, JNI_ABORT);
  free(ex);
  if (rc != PII_OK) {
    throw_code(env, rc);
    return NULL;
  }
  if (n > INT32_MAX / 4) {
    pii_spans_free(spans);
    throw_code(env, PII_ERR_NOMEM);
    return NULL;
  }
  jintArray out = (*env)->NewIntArray(env, (jsize)(4 * n));
  if (out && n) {
    jint *v = malloc(4 * n * sizeof *v);
    if (v) {
      for (size_t i = 0; i < n; i++) {
        v[4 * i] = (jint)spans[i].u16_start;
        v[4 * i + 1] = (jint)spans[i].u16_end;
        v[4 * i + 2] = spans[i].label;
        v[4 * i + 3] = spans[i].source;
      }
      (*env)->SetIntArrayRegion(env, out, 0, (jsize)(4 * n), v);
      free(v);
    } else {
      throw_code(env, PII_ERR_NOMEM);
      out = NULL;
    }
  }
  pii_spans_free(spans);
  return out;
}

JNIEXPORT jintArray JNICALL FN(detect)(JNIEnv *env, jclass cls, jlong handle, jbyteArray text, jdouble sensitivity, jint labels,
                                      jintArray extra, jboolean keep_emoji, jboolean whole_words, jboolean strict_short_names,
                                      jlong lists) {
  (void)cls;
  return run_detect(env, handle, text, sensitivity, labels, extra, keep_emoji, whole_words, strict_short_names, lists, NULL);
}

// Table mode; delimiter: 0 (auto), ',', '\t', ';' or '|' (markdown); context: PII_TABLE_CONTEXT_NONE or _HEADER.
JNIEXPORT jintArray JNICALL FN(detectTable)(JNIEnv *env, jclass cls, jlong handle, jbyteArray text, jdouble sensitivity,
                                           jint labels, jintArray extra, jboolean keep_emoji, jboolean whole_words,
                                           jboolean strict_short_names, jlong lists, jint delimiter, jboolean header, jboolean vote,
                                           jint context) {
  (void)cls;
  pii_table_options t;
  pii_table_options_init(&t);
  t.delimiter = delimiter;
  t.header = header ? 1 : 0;
  t.vote = vote ? 1 : 0;
  t.context = context;
  return run_detect(env, handle, text, sensitivity, labels, extra, keep_emoji, whole_words, strict_short_names, lists, &t);
}

// One list for pii_lists_create: entries as UTF-8 bytes back to back (lens[i] bytes each), label ids (-1: any label).
typedef struct {
  jbyteArray bytes;
  jbyte *buf;
  pii_term *terms;
  size_t n;
} term_list;

static void terms_release(JNIEnv *env, term_list *t) {
  if (t->buf) (*env)->ReleaseByteArrayElements(env, t->bytes, t->buf, JNI_ABORT);
  free(t->terms);
}

// 0, or -1 with an exception pending (t is released then).
static int terms_get(JNIEnv *env, jbyteArray bytes, jintArray lens, jintArray labels, term_list *t) {
  *t = (term_list){bytes, NULL, NULL, 0};
  jsize n = (*env)->GetArrayLength(env, lens), nb = (*env)->GetArrayLength(env, bytes);
  if ((*env)->GetArrayLength(env, labels) != n) {
    throw_code(env, PII_ERR_ARG);
    return -1;
  }
  if (!n) return 0;
  if ((size_t)n > SIZE_MAX / (2 * sizeof(jint))) {
    throw_code(env, PII_ERR_NOMEM);
    return -1;
  }
  jint *v = malloc(2 * (size_t)n * sizeof *v);
  t->terms = malloc((size_t)n * sizeof *t->terms);
  if (!v || !t->terms) {
    free(v);
    terms_release(env, t);
    throw_code(env, PII_ERR_NOMEM);
    return -1;
  }
  (*env)->GetIntArrayRegion(env, lens, 0, n, v);
  (*env)->GetIntArrayRegion(env, labels, 0, n, v + n);
  if (nb && !(t->buf = (*env)->GetByteArrayElements(env, bytes, NULL))) {
    free(v);
    terms_release(env, t);
    return -1;
  }
  size_t off = 0;
  for (jsize i = 0; i < n; i++) {
    if (v[i] < 0 || (size_t)v[i] > (size_t)nb - off) {
      free(v);
      terms_release(env, t);
      throw_code(env, PII_ERR_ARG);
      return -1;
    }
    t->terms[i] = (pii_term){t->buf ? (const char *)t->buf + off : "", (size_t)v[i], v[n + i]};
    off += (size_t)v[i];
  }
  t->n = (size_t)n;
  free(v);
  return 0;
}

// Handle of pii_lists_create (the core copies the entries), or 0 with error = [list, index, reason] for an invalid entry.
JNIEXPORT jlong JNICALL FN(listsCreate)(JNIEnv *env, jclass cls, jbyteArray allow, jintArray allow_lens, jintArray allow_labels,
                                        jbyteArray block, jintArray block_lens, jintArray block_labels, jintArray error) {
  (void)cls;
  if ((*env)->GetArrayLength(env, error) < 3) {
    throw_code(env, PII_ERR_ARG);
    return 0;
  }
  term_list a, b;
  if (terms_get(env, allow, allow_lens, allow_labels, &a)) return 0;
  if (terms_get(env, block, block_lens, block_labels, &b)) {
    terms_release(env, &a);
    return 0;
  }
  pii_lists *l = NULL;
  pii_list_error err = {-1, 0, 0};
  int rc = pii_lists_create(a.terms, a.n, b.terms, b.n, &l, &err);
  terms_release(env, &b);
  terms_release(env, &a);
  if (rc == PII_ERR_ARG && err.reason > 0) {
    jint e[3] = {err.list, (jint)err.index, err.reason};
    (*env)->SetIntArrayRegion(env, error, 0, 3, e);
    return 0;
  }
  if (rc != PII_OK) {
    throw_code(env, rc);
    return 0;
  }
  return (jlong)(intptr_t)l;
}

JNIEXPORT void JNICALL FN(listsFree)(JNIEnv *env, jclass cls, jlong handle) {
  (void)env, (void)cls;
  pii_lists_free((pii_lists *)(intptr_t)handle);
}

// [allow, block]: entries after merging duplicates.
JNIEXPORT jintArray JNICALL FN(listsCounts)(JNIEnv *env, jclass cls, jlong handle) {
  (void)cls;
  size_t na = 0, nb = 0;
  pii_lists_counts((const pii_lists *)(intptr_t)handle, &na, &nb);
  jint v[2] = {(jint)na, (jint)nb};
  jintArray out = (*env)->NewIntArray(env, 2);
  if (out) (*env)->SetIntArrayRegion(env, out, 0, 2, v);
  return out;
}

// Tokens (without <s> and </s>) as [id, utf16_start, utf16_end] * n.
JNIEXPORT jintArray JNICALL FN(tokenize)(JNIEnv *env, jclass cls, jlong handle, jbyteArray text) {
  (void)cls;
  const pii_model *m = model_of(env, handle);
  if (!m) return NULL;
  jsize len = (*env)->GetArrayLength(env, text);
  jbyte *t = len ? (*env)->GetByteArrayElements(env, text, NULL) : NULL;
  if (len && !t) return NULL;
  pii_token *tokens = NULL;
  size_t n = 0;
  int rc = pii_tokenize(m, t ? (const char *)t : "", (size_t)len, &tokens, &n);
  if (t) (*env)->ReleaseByteArrayElements(env, text, t, JNI_ABORT);
  if (rc != PII_OK) {
    throw_code(env, rc);
    return NULL;
  }
  if (n > INT32_MAX / 3) {
    pii_tokens_free(tokens);
    throw_code(env, PII_ERR_NOMEM);
    return NULL;
  }
  jintArray out = (*env)->NewIntArray(env, (jsize)(3 * n));
  if (out && n) {
    jint *v = malloc(3 * n * sizeof *v);
    if (v) {
      for (size_t i = 0; i < n; i++) {
        v[3 * i] = tokens[i].id;
        v[3 * i + 1] = (jint)tokens[i].u16_start;
        v[3 * i + 2] = (jint)tokens[i].u16_end;
      }
      (*env)->SetIntArrayRegion(env, out, 0, (jsize)(3 * n), v);
      free(v);
    } else {
      throw_code(env, PII_ERR_NOMEM);
      out = NULL;
    }
  }
  pii_tokens_free(tokens);
  return out;
}

JNIEXPORT jstring JNICALL FN(version)(JNIEnv *env, jclass cls) {
  (void)cls;
  return (*env)->NewStringUTF(env, pii_version());
}

JNIEXPORT jstring JNICALL FN(buildInfo)(JNIEnv *env, jclass cls) {
  (void)cls;
  return (*env)->NewStringUTF(env, pii_build_info());
}

// Published file pin of the core as "path\nbytes\nsha256", or null (unknown size or file).
JNIEXPORT jstring JNICALL FN(filePin)(JNIEnv *env, jclass cls, jstring size, jstring file) {
  (void)cls;
  const char *s = (*env)->GetStringUTFChars(env, size, NULL);
  if (!s) return NULL;
  const char *f = (*env)->GetStringUTFChars(env, file, NULL);
  if (!f) {
    (*env)->ReleaseStringUTFChars(env, size, s);
    return NULL;
  }
  const pii_file_pin *p = pii_model_file(s, f);
  (*env)->ReleaseStringUTFChars(env, file, f);
  (*env)->ReleaseStringUTFChars(env, size, s);
  if (!p) return NULL;
  char buf[512];
  int k = snprintf(buf, sizeof buf, "%s\n%llu\n%s", p->path, (unsigned long long)p->bytes, p->sha256);
  if (k < 0 || (size_t)k >= sizeof buf) return NULL;
  return (*env)->NewStringUTF(env, buf);
}
