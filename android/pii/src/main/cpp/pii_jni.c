// JNI bridge of ai.newmean.pii.internal.NativeBridge over the native core (native/core/pii.h).
// Text arrives as standard UTF-8 bytes (ai.newmean.pii.internal.Utf8), never through GetStringUTFChars: its modified UTF-8
// splits emoji into surrogates and NUL into two bytes, which changes tokens and offsets.
#include <jni.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "pii.h"

#define FN(name) Java_ai_newmean_pii_internal_NativeBridge_##name

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

// Spans as [utf16_start, utf16_end, label, source] * n. extra: [utf16_start, utf16_end, label] * n (pattern spans) or null.
JNIEXPORT jintArray JNICALL FN(detect)(JNIEnv *env, jclass cls, jlong handle, jbyteArray text, jdouble sensitivity, jint labels,
                                      jintArray extra) {
  (void)cls;
  const pii_model *m = model_of(env, handle);
  if (!m) return NULL;
  pii_options o;
  pii_options_init(&o);
  o.sensitivity = sensitivity;
  o.labels = (uint32_t)labels;
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
  int rc = pii_detect(m, t ? (const char *)t : "", (size_t)len, &o, &spans, &n);
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
