/* newmean_pii._core: CPython binding of the native core (native/core/pii.h).
 *
 * Limited API (abi3, Python 3.9+) unless built for a free-threaded interpreter. Text goes in as UTF-8 with
 * "surrogatepass", so code point offsets from the core equal Python str indices (lone surrogates included). The GIL is
 * released while the core loads, tokenizes and runs the model; a pii_model is immutable and safe to share across threads. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <stdlib.h>

#include "pii.h"

typedef struct {
  PyObject_HEAD
  pii_model *model;
} ModelObject;

static PyObject *raise_code(int code) {
  if (code == PII_ERR_NOMEM) return PyErr_NoMemory();
  PyErr_SetString(code == PII_ERR_ARG ? PyExc_ValueError : PyExc_RuntimeError, pii_strerror(code));
  return NULL;
}

/* str -> bytes (UTF-8, surrogatepass); the caller owns the reference. */
static PyObject *encode(PyObject *text) {
  return PyUnicode_AsEncodedString(text, "utf-8", "surrogatepass");
}

static PyObject *Model_new(PyTypeObject *type, PyObject *args, PyObject *kwargs) {
  PyObject *onnx, *tok;
  static char *kw[] = {"onnx", "tokenizer", NULL};
  if (!PyArg_ParseTupleAndKeywords(args, kwargs, "SS:Model", kw, &onnx, &tok)) return NULL;
  char *onnx_p, *tok_p;
  Py_ssize_t onnx_n, tok_n;
  if (PyBytes_AsStringAndSize(onnx, &onnx_p, &onnx_n) < 0 || PyBytes_AsStringAndSize(tok, &tok_p, &tok_n) < 0) return NULL;
  char err[256] = "";
  pii_model *m;
  Py_BEGIN_ALLOW_THREADS
  m = pii_model_load((const uint8_t *)onnx_p, (size_t)onnx_n, (const uint8_t *)tok_p, (size_t)tok_n, err, sizeof err);
  Py_END_ALLOW_THREADS
  if (!m) {
    PyErr_Format(PyExc_ValueError, "cannot load the model: %s", err[0] ? err : "unknown error");
    return NULL;
  }
  allocfunc alloc = (allocfunc)PyType_GetSlot(type, Py_tp_alloc);
  ModelObject *self = (ModelObject *)alloc(type, 0);
  if (!self) {
    pii_model_free(m);
    return NULL;
  }
  self->model = m;
  return (PyObject *)self;
}

static void Model_dealloc(PyObject *obj) {
  ModelObject *self = (ModelObject *)obj;
  PyTypeObject *type = Py_TYPE(obj);
  pii_model_free(self->model);
  self->model = NULL;
  freefunc tp_free = (freefunc)PyType_GetSlot(type, Py_tp_free);
  tp_free(obj);
  Py_DECREF(type);
}

/* detect(text, sensitivity, labels, extra=None) -> [(start, end, label_id, source_bits)], code point offsets.
 * extra: list of (start, end, label_id) in code points, merged as the npm package merges its regex layer. */
static PyObject *Model_detect(PyObject *obj, PyObject *args) {
  ModelObject *self = (ModelObject *)obj;
  PyObject *text, *extra = Py_None;
  double sensitivity;
  unsigned int labels;
  if (!PyArg_ParseTuple(args, "UdI|O:detect", &text, &sensitivity, &labels, &extra)) return NULL;
  pii_options o;
  pii_options_init(&o);
  o.sensitivity = sensitivity;
  o.labels = labels;
  o.extra_unit = PII_UNIT_CODEPOINT;
  pii_extra_span *ex = NULL;
  if (extra != Py_None) {
    if (!PyList_Check(extra)) {
      PyErr_SetString(PyExc_TypeError, "extra must be a list of (start, end, label_id) tuples");
      return NULL;
    }
    Py_ssize_t n = PyList_Size(extra);
    if (n > 0) {
      ex = (pii_extra_span *)malloc((size_t)n * sizeof *ex);
      if (!ex) return PyErr_NoMemory();
      for (Py_ssize_t i = 0; i < n; i++) {
        Py_ssize_t s, e;
        int label;
        PyObject *t = PyList_GetItem(extra, i);
        if (!t || !PyArg_ParseTuple(t, "nni", &s, &e, &label)) {
          free(ex);
          return NULL;
        }
        if (s < 0 || e < 0) {
          free(ex);
          PyErr_SetString(PyExc_ValueError, "negative offset in extra");
          return NULL;
        }
        ex[i].start = (size_t)s;
        ex[i].end = (size_t)e;
        ex[i].label = label;
      }
      o.extra = ex;
      o.n_extra = (size_t)n;
    }
  }
  PyObject *bytes = encode(text);
  if (!bytes) {
    free(ex);
    return NULL;
  }
  char *p;
  Py_ssize_t len;
  if (PyBytes_AsStringAndSize(bytes, &p, &len) < 0) {
    Py_DECREF(bytes);
    free(ex);
    return NULL;
  }
  pii_span *spans = NULL;
  size_t n_spans = 0;
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = pii_detect(self->model, p, (size_t)len, &o, &spans, &n_spans);
  Py_END_ALLOW_THREADS
  Py_DECREF(bytes);
  free(ex);
  if (rc != PII_OK) return raise_code(rc);
  PyObject *out = PyList_New((Py_ssize_t)n_spans);
  for (size_t i = 0; out && i < n_spans; i++) {
    PyObject *t = Py_BuildValue("(nnii)", (Py_ssize_t)spans[i].cp_start, (Py_ssize_t)spans[i].cp_end, spans[i].label, spans[i].source);
    if (!t) Py_CLEAR(out);
    else PyList_SetItem(out, (Py_ssize_t)i, t);
  }
  pii_spans_free(spans);
  return out;
}

/* tokenize(text) -> [(id, start, end)], code point offsets, without <s> and </s>. */
static PyObject *Model_tokenize(PyObject *obj, PyObject *args) {
  ModelObject *self = (ModelObject *)obj;
  PyObject *text;
  if (!PyArg_ParseTuple(args, "U:tokenize", &text)) return NULL;
  PyObject *bytes = encode(text);
  if (!bytes) return NULL;
  char *p;
  Py_ssize_t len;
  if (PyBytes_AsStringAndSize(bytes, &p, &len) < 0) {
    Py_DECREF(bytes);
    return NULL;
  }
  pii_token *toks = NULL;
  size_t n = 0;
  int rc;
  Py_BEGIN_ALLOW_THREADS
  rc = pii_tokenize(self->model, p, (size_t)len, &toks, &n);
  Py_END_ALLOW_THREADS
  Py_DECREF(bytes);
  if (rc != PII_OK) return raise_code(rc);
  PyObject *out = PyList_New((Py_ssize_t)n);
  for (size_t i = 0; out && i < n; i++) {
    PyObject *t = Py_BuildValue("(inn)", (int)toks[i].id, (Py_ssize_t)toks[i].cp_start, (Py_ssize_t)toks[i].cp_end);
    if (!t) Py_CLEAR(out);
    else PyList_SetItem(out, (Py_ssize_t)i, t);
  }
  pii_tokens_free(toks);
  return out;
}

static PyObject *Model_info(PyObject *obj, PyObject *unused) {
  (void)unused;
  pii_model_info i;
  pii_model_get_info(((ModelObject *)obj)->model, &i);
  return Py_BuildValue("{s:i,s:i,s:i,s:i,s:i,s:i,s:s}", "hidden", i.hidden, "layers", i.layers, "layer_applications",
                       i.layer_applications, "vocab", i.vocab, "max_tokens", i.max_tokens, "tags", i.tags, "embedding",
                       i.embedding_kind == 2 ? "int4" : "int8");
}

static PyMethodDef Model_methods[] = {
    {"detect", Model_detect, METH_VARARGS, "detect(text, sensitivity, labels, extra=None) -> [(start, end, label_id, source)]"},
    {"tokenize", Model_tokenize, METH_VARARGS, "tokenize(text) -> [(id, start, end)]"},
    {"info", Model_info, METH_NOARGS, "Model shape."},
    {NULL, NULL, 0, NULL},
};

static PyType_Slot Model_slots[] = {
    {Py_tp_new, (void *)Model_new},
    {Py_tp_dealloc, (void *)Model_dealloc},
    {Py_tp_methods, Model_methods},
    {Py_tp_doc, "Model(onnx: bytes, tokenizer: bytes): a loaded model (verify the files first)."},
    {0, NULL},
};

static PyType_Spec Model_spec = {"newmean_pii._core.Model", sizeof(ModelObject), 0, Py_TPFLAGS_DEFAULT, Model_slots};

static PyObject *f_version(PyObject *m, PyObject *unused) {
  (void)m, (void)unused;
  return PyUnicode_FromString(pii_version());
}

static PyObject *f_build_info(PyObject *m, PyObject *unused) {
  (void)m, (void)unused;
  return PyUnicode_FromString(pii_build_info());
}

/* model_file(size, file) -> (path, bytes, sha256) or None */
static PyObject *f_model_file(PyObject *m, PyObject *args) {
  (void)m;
  const char *size, *file;
  if (!PyArg_ParseTuple(args, "ss:model_file", &size, &file)) return NULL;
  const pii_file_pin *pin = pii_model_file(size, file);
  if (!pin) Py_RETURN_NONE;
  return Py_BuildValue("(sKs)", pin->path, (unsigned long long)pin->bytes, pin->sha256);
}

static PyObject *f_default_variant(PyObject *m, PyObject *args) {
  (void)m;
  const char *size;
  if (!PyArg_ParseTuple(args, "s:default_variant", &size)) return NULL;
  const char *v = pii_default_variant(size);
  if (!v) Py_RETURN_NONE;
  return PyUnicode_FromString(v);
}

static PyMethodDef module_methods[] = {
    {"version", f_version, METH_NOARGS, "Version of the native core."},
    {"build_info", f_build_info, METH_NOARGS, "Kernel build (\"neon\", \"sse\", \"vector\" or \"scalar\", \"+fma\")."},
    {"model_file", f_model_file, METH_VARARGS, "model_file(size, file) -> (path, bytes, sha256) or None"},
    {"default_variant", f_default_variant, METH_VARARGS, "default_variant(size) -> \"q4\" | \"q4e\" | None"},
    {NULL, NULL, 0, NULL},
};

static int module_exec(PyObject *mod) {
  PyObject *type = PyType_FromSpec(&Model_spec);
  if (!type || PyModule_AddObject(mod, "Model", type) < 0) {
    Py_XDECREF(type);
    return -1;
  }
  PyObject *labels = PyTuple_New(PII_LABEL_COUNT);
  if (!labels) return -1;
  for (int i = 0; i < PII_LABEL_COUNT; i++) {
    PyObject *s = PyUnicode_FromString(pii_label_name(i));
    if (!s) {
      Py_DECREF(labels);
      return -1;
    }
    PyTuple_SetItem(labels, i, s);
  }
  if (PyModule_AddObject(mod, "LABELS", labels) < 0) {
    Py_DECREF(labels);
    return -1;
  }
  if (PyModule_AddStringConstant(mod, "VERSION", PII_VERSION) < 0 ||
      PyModule_AddStringConstant(mod, "MODEL_VERSION", PII_MODEL_VERSION) < 0 ||
      PyModule_AddStringConstant(mod, "DEFAULT_SIZE", PII_DEFAULT_SIZE) < 0 ||
      PyModule_AddIntConstant(mod, "LABELS_ALL", PII_LABELS_ALL) < 0 ||
      PyModule_AddIntConstant(mod, "SOURCE_MODEL", PII_SOURCE_MODEL) < 0 ||
      PyModule_AddIntConstant(mod, "SOURCE_REGEX", PII_SOURCE_REGEX) < 0)
    return -1;
  return 0;
}

static PyModuleDef_Slot module_slots[] = {
    {Py_mod_exec, (void *)module_exec},
#ifdef Py_GIL_DISABLED
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
#endif
    {0, NULL},
};

static struct PyModuleDef module_def = {
    PyModuleDef_HEAD_INIT, "_core", "Native core of newmean-pii (see newmean_pii).", 0, module_methods, module_slots, NULL, NULL, NULL,
};

PyMODINIT_FUNC PyInit__core(void) { return PyModuleDef_Init(&module_def); }
