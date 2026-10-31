// Unigram tokenizer, a port of js/src/tokenizer.ts (which reproduces the Hugging Face `tokenizers` pipeline of the model):
// Precompiled (SentencePiece) normalizer over extended grapheme clusters -> WhitespaceSplit -> Metaspace("▁", prepend
// always, split) -> Unigram (Viterbi). Works on code points; offsets are code point indices of the original text.
#include <stdlib.h>
#include <string.h>

#include "internal.h"
#include "unicode_tables.h"

#define META 0x2581u  // "▁"
#define UNK_PENALTY 10.0
#define MAX_SPECIAL 16

typedef struct {
  uint32_t *cps;
  size_t n;
  int id, lstrip, rstrip;
} special;

struct pii_tok {
  int unk, bos, eos, pad;
  size_t vocab;
  float *scores;
  double unk_score;
  // piece trie: node 0 is the root; child of (node, code point) via an open-addressing hash
  int32_t *node_id;
  size_t nodes;
  uint64_t *hkeys;  // (node + 1) << 21 | code point; 0 = empty
  int32_t *hvals;
  size_t hmask;
  // precompiled charsmap
  uint32_t *trie;
  size_t trie_n;
  uint8_t *norm;
  size_t norm_n;
  special sp[MAX_SPECIAL];
  int nsp;
};

int pii_tok_bos(const pii_tok *t) { return t->bos; }
int pii_tok_eos(const pii_tok *t) { return t->eos; }
int pii_tok_pad(const pii_tok *t) { return t->pad; }

// ---------------------------------------------------------------- Unicode helpers
// Rust char::is_whitespace (Unicode White_Space), which is what WhitespaceSplit uses
static int is_ws(uint32_t c) {
  return (c >= 9 && c <= 13) || c == 0x20 || c == 0x85 || c == 0xa0 || c == 0x1680 || (c >= 0x2000 && c <= 0x200a) ||
         c == 0x2028 || c == 0x2029 || c == 0x202f || c == 0x205f || c == 0x3000;
}

static int gcat(uint32_t c) {
  size_t lo = 0, hi = sizeof PII_GC_TABLE / sizeof PII_GC_TABLE[0];
  while (lo < hi) {
    size_t mid = (lo + hi) / 2;
    if (c < PII_GC_TABLE[mid].lo) hi = mid;
    else if (c > PII_GC_TABLE[mid].hi) lo = mid + 1;
    else return PII_GC_TABLE[mid].cat;
  }
  return GC_Any;
}

static int incb_extend(uint32_t c) {
  size_t lo = 0, hi = sizeof PII_INCB_EXTEND / sizeof PII_INCB_EXTEND[0];
  while (lo < hi) {
    size_t mid = (lo + hi) / 2;
    if (c < PII_INCB_EXTEND[mid][0]) hi = mid;
    else if (c > PII_INCB_EXTEND[mid][1]) lo = mid + 1;
    else return 1;
  }
  return 0;
}

static int incb_linker(uint32_t c) {
  for (size_t i = 0; i < sizeof PII_INCB_LINKER / sizeof PII_INCB_LINKER[0]; i++)
    if (PII_INCB_LINKER[i] == c) return 1;
  return 0;
}

// End of the extended grapheme cluster that starts at cps[i] (UAX #29, Unicode 17; as unicode-segmentation and ICU).
static size_t grapheme_end(const uint32_t *cps, size_t i, size_t n) {
  int p = gcat(cps[i]);
  int emoji = p == GC_Extended_Pictographic;  // 1: ExtPict Extend*, 2: ExtPict Extend* ZWJ
  int incb = p == GC_InCB_Consonant;          // 1: Consonant [Extend Linker]*, 2: ... with a Linker
  int ri = p == GC_Regional_Indicator;        // RIs in a row ending at the previous character
  size_t j = i + 1;
  for (; j < n; j++) {
    const uint32_t ch = cps[j];
    const int c = gcat(ch);
    int keep;
    if (p == GC_CR && c == GC_LF) keep = 1;                                                       // GB3
    else if (p == GC_Control || p == GC_CR || p == GC_LF) keep = 0;                               // GB4
    else if (c == GC_Control || c == GC_CR || c == GC_LF) keep = 0;                               // GB5
    else if (p == GC_L && (c == GC_L || c == GC_V || c == GC_LV || c == GC_LVT)) keep = 1;        // GB6
    else if ((p == GC_LV || p == GC_V) && (c == GC_V || c == GC_T)) keep = 1;                     // GB7
    else if ((p == GC_LVT || p == GC_T) && c == GC_T) keep = 1;                                   // GB8
    else if (c == GC_Extend || c == GC_ZWJ || c == GC_SpacingMark || p == GC_Prepend) keep = 1;  // GB9, 9a, 9b
    else if (c == GC_InCB_Consonant && incb == 2) keep = 1;                                       // GB9c
    else if (p == GC_ZWJ && c == GC_Extended_Pictographic && emoji == 2) keep = 1;                // GB11
    else if (p == GC_Regional_Indicator && c == GC_Regional_Indicator && (ri & 1)) keep = 1;      // GB12, GB13
    else keep = 0;                                                                                // GB999
    if (!keep) break;
    emoji = c == GC_Extended_Pictographic ? 1 : (emoji == 1 && c == GC_Extend) ? 1 : (emoji == 1 && c == GC_ZWJ) ? 2 : 0;
    incb = c == GC_InCB_Consonant ? 1 : (incb && incb_linker(ch)) ? 2 : (incb && incb_extend(ch)) ? incb : 0;
    ri = c == GC_Regional_Indicator ? ri + 1 : 0;
    p = c;
  }
  return j;
}

static size_t utf8_len(uint32_t c) { return c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4; }

// UTF-8 as TextEncoder writes it (lone surrogates become U+FFFD)
static size_t encode_utf8(uint32_t c, uint8_t *o) {
  if (c >= 0xd800 && c < 0xe000) c = 0xfffd;
  if (c < 0x80) {
    o[0] = (uint8_t)c;
    return 1;
  }
  if (c < 0x800) {
    o[0] = (uint8_t)(0xc0 | (c >> 6));
    o[1] = (uint8_t)(0x80 | (c & 0x3f));
    return 2;
  }
  if (c < 0x10000) {
    o[0] = (uint8_t)(0xe0 | (c >> 12));
    o[1] = (uint8_t)(0x80 | ((c >> 6) & 0x3f));
    o[2] = (uint8_t)(0x80 | (c & 0x3f));
    return 3;
  }
  o[0] = (uint8_t)(0xf0 | (c >> 18));
  o[1] = (uint8_t)(0x80 | ((c >> 12) & 0x3f));
  o[2] = (uint8_t)(0x80 | ((c >> 6) & 0x3f));
  o[3] = (uint8_t)(0x80 | (c & 0x3f));
  return 4;
}

typedef struct {
  uint32_t ch;
  size_t s, e;
} aligned;

typedef struct {  // a normalized character and its alignment change (tokenizers' NormalizedString::transform)
  uint32_t ch;
  int32_t change;  // 0: replaces one original character, > 0: inserted, < 0: replaces 1 - change characters
} tchar;

// Replacement characters with their alignment changes, as `replace` in tokenizers' precompiled.rs: every new character
// has change 0, except that the last (new - old) characters get +1 when the replacement is longer, and the last pushed
// character (possibly from an earlier part) absorbs the difference when it is shorter. Code points as TextDecoder reads
// the UTF-8 bytes (each maximal invalid subpart becomes one U+FFFD).
static int push_replacement(pii_vec *out, const uint8_t *s, size_t n, size_t old_count) {
  const size_t first = out->n;
  size_t i = 0;
  while (i < n) {
    uint8_t c = s[i];
    uint32_t v = 0xfffd;
    size_t j = i + 1;
    if (c < 0x80) v = c;
    else {
      int need = c >= 0xc2 && c < 0xe0 ? 1 : c >= 0xe0 && c < 0xf0 ? 2 : c >= 0xf0 && c < 0xf5 ? 3 : -1;
      if (need > 0) {
        uint8_t lo = c == 0xe0 ? 0xa0 : c == 0xf0 ? 0x90 : 0x80, hi = c == 0xed ? 0x9f : c == 0xf4 ? 0x8f : 0xbf;
        uint32_t x = c & (0x3f >> need);
        int t = 0;
        for (; t < need; t++, j++) {
          if (j >= n || s[j] < (t == 0 ? lo : 0x80) || s[j] > (t == 0 ? hi : 0xbf)) break;
          x = (x << 6) | (s[j] & 0x3f);
        }
        if (t == need) v = x;
      }
    }
    tchar *e = pii_vec_push(out);
    if (!e) return -1;
    e->ch = v;
    i = j;
  }
  const size_t added = out->n - first;
  tchar *tc = out->p;
  if (added > old_count)
    for (size_t k = out->n - (added - old_count); k < out->n; k++) tc[k].change = 1;
  else if (added < old_count && out->n)
    tc[out->n - 1].change -= (int32_t)(old_count - added);
  return 0;
}

// ---------------------------------------------------------------- charsmap
// Replacement for the shortest key that is a prefix of `key` (same as the Rust `spm_precompiled` crate): returns 1 and
// the replacement bytes, or 0. Walks the darts-clone double array exactly like CharsMap.transform in tokenizer.ts.
static int transform(const pii_tok *t, const uint8_t *key, size_t n, const uint8_t **rep, size_t *rep_n) {
  const uint32_t *a = t->trie;
  if (!t->trie_n) return 0;
#define OFFSET(u) (((u) >> 10) << (((u) & (1u << 9)) >> 6))
  size_t pos = OFFSET(a[0]);
  for (size_t i = 0; i < n; i++) {
    const uint32_t c = key[i];
    pos ^= c;
    if (pos >= t->trie_n) return 0;
    const uint32_t u = a[pos];
    if ((u & 0x800000ffu) != c) return 0;  // label
    pos ^= OFFSET(u);
    if ((u >> 8) & 1) {
      if (pos >= t->trie_n) return 0;
      size_t start = a[pos] & 0x7fffffffu, end = start;  // value
      if (start > t->norm_n) start = end = t->norm_n;
      while (end < t->norm_n && t->norm[end] != 0) end++;
      *rep = t->norm + start;
      *rep_n = end - start;
      return 1;
    }
  }
#undef OFFSET
  return 0;
}

// ---------------------------------------------------------------- piece trie
static uint64_t hkey(int32_t node, uint32_t cp) { return ((uint64_t)(node + 1) << 21) | cp; }
static size_t hslot(uint64_t k, size_t mask) {
  k ^= k >> 33;
  k *= 0xff51afd7ed558ccdull;
  k ^= k >> 33;
  return (size_t)k & mask;
}
static int32_t child(const pii_tok *t, int32_t node, uint32_t cp) {
  const uint64_t k = hkey(node, cp);
  for (size_t s = hslot(k, t->hmask);; s = (s + 1) & t->hmask) {
    if (t->hkeys[s] == k) return t->hvals[s];
    if (!t->hkeys[s]) return -1;
  }
}

// ---------------------------------------------------------------- loading
void pii_tok_free(pii_tok *t) {
  if (!t) return;
  free(t->scores);
  free(t->node_id);
  free(t->hkeys);
  free(t->hvals);
  free(t->trie);
  free(t->norm);
  for (int i = 0; i < t->nsp; i++) free(t->sp[i].cps);
  free(t);
}

static int num_field(const pii_json *root, const char *k, int *out) {
  const pii_json *v = pii_json_get(root, k);
  if (!v || v->type != PJ_NUM) return -1;
  *out = (int)v->num;
  return 0;
}

pii_tok *pii_tok_load(const uint8_t *json, size_t n, char *err, size_t errlen) {
  pii_json root;
  if (pii_json_parse((const char *)json, n, &root)) {
    pii_set_err(err, errlen, "tokenizer: invalid JSON");
    return NULL;
  }
  pii_tok *t = calloc(1, sizeof *t);
  uint32_t *cps = NULL;
  if (!t) goto oom;
  const pii_json *fmt = pii_json_get(&root, "format"), *type = pii_json_get(&root, "type");
  const pii_json *pieces = pii_json_get(&root, "pieces"), *scores = pii_json_get(&root, "scores");
  const pii_json *charsmap = pii_json_get(&root, "charsmap"), *spec = pii_json_get(&root, "special");
  if (!fmt || fmt->type != PJ_NUM || fmt->num != 1 || !type || type->type != PJ_STR || strcmp(type->str, "unigram") != 0) {
    pii_set_err(err, errlen, "unsupported tokenizer file");
    goto fail;
  }
  if (!pieces || pieces->type != PJ_STR || !scores || scores->type != PJ_STR || !charsmap || charsmap->type != PJ_STR ||
      !spec || spec->type != PJ_ARR || num_field(&root, "unk_id", &t->unk) || num_field(&root, "bos_id", &t->bos) ||
      num_field(&root, "eos_id", &t->eos) || num_field(&root, "pad_id", &t->pad)) {
    pii_set_err(err, errlen, "tokenizer: missing fields");
    goto fail;
  }
  // special tokens, longest first (stable)
  for (size_t i = 0; i < spec->n; i++) {
    const pii_json *s = &spec->items[i], *c = pii_json_get(s, "content"), *id = pii_json_get(s, "id");
    const pii_json *ls = pii_json_get(s, "lstrip"), *rs = pii_json_get(s, "rstrip");
    if (!c || c->type != PJ_STR || !c->len || !id || id->type != PJ_NUM || t->nsp == MAX_SPECIAL) {
      pii_set_err(err, errlen, "tokenizer: bad special token");
      goto fail;
    }
    special *sp = &t->sp[t->nsp++];
    pii_text tx;
    if (pii_text_init(&tx, c->str, c->len)) goto oom;
    sp->cps = tx.cp;
    sp->n = tx.n;
    tx.cp = NULL;
    pii_text_free(&tx);
    sp->id = (int)id->num;
    sp->lstrip = ls && ls->type == PJ_BOOL && ls->num != 0;
    sp->rstrip = rs && rs->type == PJ_BOOL && rs->num != 0;
  }
  for (int i = 1; i < t->nsp; i++)
    for (int j = i; j > 0 && t->sp[j].n > t->sp[j - 1].n; j--) {
      special tmp = t->sp[j];
      t->sp[j] = t->sp[j - 1];
      t->sp[j - 1] = tmp;
    }
  // pieces: newline-separated, index = id
  size_t count = 1;
  for (size_t i = 0; i < pieces->len; i++) count += pieces->str[i] == '\n';
  t->vocab = count;
  size_t raw_n;
  uint8_t *raw = pii_base64(scores->str, scores->len, &raw_n);
  if (!raw) goto bad64;
  t->scores = calloc(count, sizeof(float));
  if (!t->scores) {
    free(raw);
    goto oom;
  }
  for (size_t i = 0; i < count && (i + 1) * 4 <= raw_n; i++) memcpy(&t->scores[i], raw + i * 4, 4);
  free(raw);
  double mn = 1.0 / 0.0;
  for (size_t i = 0; i < count; i++) mn = t->scores[i] < mn ? t->scores[i] : mn;
  t->unk_score = mn - UNK_PENALTY;
  // trie nodes: at most one per piece character, plus the root
  size_t cap = pieces->len + 2;
  t->node_id = malloc(cap * sizeof(int32_t));
  size_t hcap = 16;
  while (hcap < cap * 2) hcap *= 2;
  t->hkeys = calloc(hcap, sizeof(uint64_t));
  t->hvals = malloc(hcap * sizeof(int32_t));
  cps = malloc((pieces->len + 1) * sizeof(uint32_t));
  if (!t->node_id || !t->hkeys || !t->hvals || !cps) goto oom;
  t->hmask = hcap - 1;
  t->node_id[0] = -1;
  t->nodes = 1;
  const char *p = pieces->str, *end = pieces->str + pieces->len;
  for (int32_t id = 0; p <= end; id++) {
    const char *q = p;
    while (q < end && *q != '\n') q++;
    int is_special = 0;
    for (int k = 0; k < t->nsp; k++) is_special |= t->sp[k].id == id;
    if (!is_special) {
      pii_text tx;
      if (pii_text_init(&tx, p, (size_t)(q - p))) goto oom;
      int32_t node = 0;
      for (size_t k = 0; k < tx.n; k++) {
        int32_t c = child(t, node, tx.cp[k]);
        if (c < 0) {
          const uint64_t key = hkey(node, tx.cp[k]);
          size_t s = hslot(key, t->hmask);
          while (t->hkeys[s]) s = (s + 1) & t->hmask;
          t->hkeys[s] = key;
          t->hvals[s] = c = (int32_t)t->nodes;
          t->node_id[t->nodes++] = -1;
        }
        node = c;
      }
      t->node_id[node] = id;
      pii_text_free(&tx);
    }
    p = q + 1;
  }
  // charsmap: u32 trie size, trie, NUL-terminated replacement strings
  size_t cm_n;
  uint8_t *cm = pii_base64(charsmap->str, charsmap->len, &cm_n);
  if (!cm) goto bad64;
  uint32_t trie_size = 0;
  if (cm_n >= 4) memcpy(&trie_size, cm, 4);
  if (cm_n < 4 || trie_size > cm_n - 4) {
    free(cm);
    pii_set_err(err, errlen, "tokenizer: bad charsmap");
    goto fail;
  }
  t->trie_n = trie_size / 4;
  t->trie = malloc(t->trie_n * 4 + 4);
  t->norm_n = cm_n - 4 - trie_size;
  t->norm = malloc(t->norm_n + 1);
  if (!t->trie || !t->norm) {
    free(cm);
    goto oom;
  }
  memcpy(t->trie, cm + 4, t->trie_n * 4);
  memcpy(t->norm, cm + 4 + trie_size, t->norm_n);
  free(cm);
  free(cps);
  pii_json_free(&root);
  return t;
bad64:
  pii_set_err(err, errlen, "tokenizer: bad base64");
  goto fail;
oom:
  pii_set_err(err, errlen, "out of memory");
fail:
  free(cps);
  pii_tok_free(t);
  pii_json_free(&root);
  return NULL;
}

// ---------------------------------------------------------------- encoding
typedef struct {
  pii_vec trans;  // tchar
  pii_vec chars;  // aligned
  pii_vec word;   // aligned: a piece with "▁" prepended
  pii_vec score;  // double
  pii_vec bid;    // int32_t
  pii_vec bstart; // int32_t
  pii_vec toks;   // int32_t triples
  int unk_prev;
} scratch;

// Normalized characters of cps[a, b), each aligned to the original character it came from. Graphemes and characters are
// replaced as tokenizers' Precompiled normalizer does, then aligned like NormalizedString::transform: a character with
// change <= 0 takes the range of the original character under the cursor and moves it 1 - change characters on; an
// inserted one (change > 0) takes the range of the character before the cursor. (tokenizer.ts instead aligns every
// character of a replaced grapheme to the whole grapheme, which differs from Hugging Face on a few combining sequences.)
static int normalize(const pii_tok *t, const uint32_t *cps, size_t a, size_t b, pii_vec *trans, pii_vec *out) {
  uint8_t key[64];
  trans->n = 0;
  for (size_t i = a; i < b;) {
    const size_t end = grapheme_end(cps, i, b);
    size_t bytes = 0;
    for (size_t k = i; k < end; k++) bytes += utf8_len(cps[k]);
    if (bytes < 6) {
      size_t kn = 0;
      for (size_t k = i; k < end; k++) kn += encode_utf8(cps[k], key + kn);
      const uint8_t *rep;
      size_t rep_n;
      if (transform(t, key, kn, &rep, &rep_n)) {
        if (push_replacement(trans, rep, rep_n, end - i)) return -1;
        i = end;
        continue;
      }
    }
    for (size_t k = i; k < end; k++) {
      const size_t kn = encode_utf8(cps[k], key);
      const uint8_t *rep;
      size_t rep_n;
      if (transform(t, key, kn, &rep, &rep_n)) {
        if (push_replacement(trans, rep, rep_n, 1)) return -1;
      } else {
        tchar *e = pii_vec_push(trans);
        if (!e) return -1;
        e->ch = cps[k];
      }
    }
    i = end;
  }
  if (pii_vec_reserve(out, trans->n)) return -1;
  const tchar *tc = trans->p;
  aligned *o = out->p;
  size_t cur = a;
  for (size_t k = 0; k < trans->n; k++) {
    o[k].ch = tc[k].ch;
    if (tc[k].change > 0) {
      o[k].s = cur > a ? cur - 1 : a;  // tokenizers: the previous character's alignment, or (0, 0) at the start
      o[k].e = cur > a ? cur : a;
    } else {
      o[k].s = cur < b ? cur : b;
      o[k].e = cur < b ? cur + 1 : b;
      cur += 1 + (size_t)(-(int64_t)tc[k].change);
    }
  }
  out->n = trans->n;
  return 0;
}

static int viterbi(const pii_tok *t, const aligned *w, size_t n, scratch *sc, pii_vec *ids, pii_vec *st, pii_vec *en) {
  if (pii_vec_reserve(&sc->score, n + 1) || pii_vec_reserve(&sc->bid, n + 1) || pii_vec_reserve(&sc->bstart, n + 1)) return -1;
  double *best = sc->score.p;
  int32_t *bid = sc->bid.p, *bstart = sc->bstart.p;
  for (size_t i = 0; i <= n; i++) {
    best[i] = -1.0 / 0.0;
    bid[i] = -1;
    bstart[i] = -1;
  }
  best[0] = 0;
  for (size_t i = 0; i < n; i++) {
    if (bstart[i] < 0 && i > 0) continue;
    const double base = best[i];
    int32_t node = 0;
    int single = 0;
    for (size_t j = i; j < n; j++) {
      node = child(t, node, w[j].ch);
      if (node < 0) break;
      const int32_t id = t->node_id[node];
      if (id >= 0) {
        const double cand = base + (double)t->scores[id];
        if (bstart[j + 1] < 0 || cand > best[j + 1]) {
          best[j + 1] = cand;
          bid[j + 1] = id;
          bstart[j + 1] = (int32_t)i;
        }
        if (j == i) single = 1;
      }
    }
    if (!single) {
      const double cand = base + t->unk_score;
      if (bstart[i + 1] < 0 || cand > best[i + 1]) {
        best[i + 1] = cand;
        bid[i + 1] = t->unk;
        bstart[i + 1] = (int32_t)i;
      }
    }
  }
  sc->toks.n = 0;
  sc->unk_prev = 0;  // fuse_unk works within one piece
  for (size_t e = n; e > 0; e = (size_t)bstart[e]) {
    int32_t *tk = pii_vec_push(&sc->toks);
    if (!tk) return -1;
    tk[0] = bid[e];
    tk[1] = bstart[e];
    tk[2] = (int32_t)e;
  }
  const int32_t *toks = sc->toks.p;
  for (size_t k = sc->toks.n; k-- > 0;) {
    const int32_t id = toks[3 * k], a = toks[3 * k + 1], b = toks[3 * k + 2];
    size_t s = (size_t)-1, e = 0;
    for (int32_t x = a; x < b; x++) {
      if (w[x].s < s) s = w[x].s;
      if (w[x].e > e) e = w[x].e;
    }
    if (id == t->unk && sc->unk_prev) {  // consecutive unknown characters become one <unk> (fuse_unk)
      int32_t *ls = (int32_t *)st->p + st->n - 1, *le = (int32_t *)en->p + en->n - 1;
      if ((int32_t)s < *ls) *ls = (int32_t)s;
      if ((int32_t)e > *le) *le = (int32_t)e;
    } else {
      int32_t *pi = pii_vec_push(ids), *ps = pii_vec_push(st), *pe = pii_vec_push(en);
      if (!pi || !ps || !pe) return -1;
      *pi = id;
      *ps = (int32_t)s;
      *pe = (int32_t)e;
    }
    sc->unk_prev = id == t->unk;
  }
  return 0;
}

// WhitespaceSplit + Metaspace (prepend "▁" aligned to the first character, split before every "▁"), then Viterbi.
static int run(const pii_tok *t, const uint32_t *cps, size_t a, size_t b, scratch *sc, pii_vec *ids, pii_vec *st, pii_vec *en) {
  if (b <= a) return 0;
  sc->chars.n = 0;
  if (normalize(t, cps, a, b, &sc->trans, &sc->chars)) return -1;
  const aligned *ch = sc->chars.p;
  const size_t n = sc->chars.n;
  for (size_t i = 0; i < n;) {
    if (is_ws(ch[i].ch)) {
      i++;
      continue;
    }
    size_t j = i;
    while (j < n && !is_ws(ch[j].ch)) j++;
    // word ch[i, j): pieces start at every "▁"; a word not starting with "▁" gets one aligned to its first character
    size_t k = i;
    while (k < j) {
      size_t e = k + 1;
      while (e < j && ch[e].ch != META) e++;
      if (k == i && ch[i].ch != META) {
        // "▁" + ch[i, e)
        if (pii_vec_reserve(&sc->word, e - k + 1)) return -1;
        aligned *w = sc->word.p;
        w[0].ch = META;
        w[0].s = ch[i].s;
        w[0].e = ch[i].e;
        memcpy(w + 1, ch + k, (e - k) * sizeof(aligned));
        if (viterbi(t, w, e - k + 1, sc, ids, st, en)) return -1;
      } else if (viterbi(t, ch + k, e - k, sc, ids, st, en)) return -1;
      k = e;
    }
    i = j;
  }
  return 0;
}

// Special-token strings in the text are matched before normalization, as in tokenizers: the leftmost match, trying the
// tokens longest first at each position (the JavaScript regex alternation); lstrip / rstrip take adjacent \s.
// An lstrip token tried at i can only start in [i, end of the \s run]; when all of them fail there, they fail at every
// later position of the run too, so *lstrip_dead (that end) lets the caller skip them: linear, not quadratic, on long runs.
static size_t match_special(const pii_tok *t, const uint32_t *cps, size_t n, size_t i, int *which, size_t *lstrip_dead) {
  const int skip_lstrip = *lstrip_dead != (size_t)-1 && i <= *lstrip_dead;
  size_t ws_end = i;
  if (!skip_lstrip)
    while (ws_end < n && pii_is_js_space(cps[ws_end])) ws_end++;
  for (int k = 0; k < t->nsp; k++) {
    const special *sp = &t->sp[k];
    if (sp->lstrip && skip_lstrip) continue;
    for (size_t st = sp->lstrip ? ws_end + 1 : i + 1; st-- > i;) {  // \s* backtracks from greedy
      if (st + sp->n <= n && memcmp(cps + st, sp->cps, sp->n * 4) == 0) {
        size_t e = st + sp->n;
        if (sp->rstrip)
          while (e < n && pii_is_js_space(cps[e])) e++;
        *which = k;
        return e;
      }
      if (!sp->lstrip) break;
    }
  }
  if (!skip_lstrip) *lstrip_dead = ws_end;
  return 0;
}

int pii_tok_encode(const pii_tok *t, const uint32_t *cps, size_t n, pii_vec *ids, pii_vec *st, pii_vec *en) {
  scratch sc;
  memset(&sc, 0, sizeof sc);
  pii_vec_init(&sc.trans, sizeof(tchar));
  pii_vec_init(&sc.chars, sizeof(aligned));
  pii_vec_init(&sc.word, sizeof(aligned));
  pii_vec_init(&sc.score, sizeof(double));
  pii_vec_init(&sc.bid, sizeof(int32_t));
  pii_vec_init(&sc.bstart, sizeof(int32_t));
  pii_vec_init(&sc.toks, 3 * sizeof(int32_t));
  int rc = 0;
  size_t last = 0, lstrip_dead = (size_t)-1;
  for (size_t i = 0; i < n && !rc;) {
    int k;
    size_t e = match_special(t, cps, n, i, &k, &lstrip_dead);
    if (!e) {
      i++;
      continue;
    }
    rc = run(t, cps, last, i, &sc, ids, st, en);
    int32_t *pi = pii_vec_push(ids), *ps = pii_vec_push(st), *pe = pii_vec_push(en);
    if (rc || !pi || !ps || !pe) {
      rc = -1;
      break;
    }
    *pi = t->sp[k].id;
    *ps = (int32_t)i;
    *pe = (int32_t)e;
    last = i = e;
  }
  if (!rc) rc = run(t, cps, last, n, &sc, ids, st, en);
  pii_vec_free(&sc.trans);
  pii_vec_free(&sc.chars);
  pii_vec_free(&sc.word);
  pii_vec_free(&sc.score);
  pii_vec_free(&sc.bid);
  pii_vec_free(&sc.bstart);
  pii_vec_free(&sc.toks);
  return rc;
}
