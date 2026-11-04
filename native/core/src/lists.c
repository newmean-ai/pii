// User lists (pii_options.lists, added in 1.4.0): validation, tries of the compared forms, block matching and the allow
// test. Same rules as js/src/lists.ts; the rules are documented with pii_lists_create in pii.h.
#include <stdlib.h>
#include <string.h>

#include "internal.h"

#define MAX_ENTRIES 10000
#define MAX_CP 64
#define MAX_U16 256
#define MAX_U8 1024

typedef struct {
  uint32_t cp;           // the edge's code point (compared form)
  uint32_t first, n;     // children nodes[first, first + n), sorted by cp
  int32_t term;          // the term ending here, or -1
} pii_node;

typedef struct {
  pii_node *nodes;
  size_t n;
} pii_trie;

struct pii_lists {
  pii_trie allow, block;
  uint32_t *allow_fam;   // per allow term: 1 << family label of each family the term allows
  int *block_label;      // per block term
  uint32_t *block_index; // per block term: its lowest original index (the winner key's index)
  size_t n_allow, n_block;
};

size_t pii_options_size(void) { return sizeof(pii_options); }
size_t pii_table_options_size(void) { return sizeof(pii_table_options); }

static uint32_t fold(uint32_t c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }
static int family(int label) { return label == PII_PERSON_L2 ? PII_PERSON : label == PII_URL_L2 ? PII_URL : label; }

// The 25 White_Space code points of Unicode 17.0.
static int white_space(uint32_t c) {
  return (c >= 0x09 && c <= 0x0d) || c == 0x20 || c == 0x85 || c == 0xa0 || c == 0x1680 || (c >= 0x2000 && c <= 0x200a) ||
         c == 0x2028 || c == 0x2029 || c == 0x202f || c == 0x205f || c == 0x3000;
}
static int line_break(uint32_t c) { return c == 0x0a || c == 0x0d || c == 0x85 || c == 0x2028 || c == 0x2029; }

// Strict UTF-8 (no overlong forms, surrogates or code points above U+10FFFF) -> folded code points in out (capacity len).
// Returns the count, or (size_t)-1 when malformed.
static size_t decode_strict(const uint8_t *s, size_t len, uint32_t *out) {
  size_t i = 0, n = 0;
  while (i < len) {
    const uint8_t c = s[i];
    uint32_t v;
    size_t k;
    if (c < 0x80) v = c, k = 1;
    else if (c >= 0xc2 && c < 0xe0) v = c & 0x1f, k = 2;
    else if (c >= 0xe0 && c < 0xf0) v = c & 0x0f, k = 3;
    else if (c >= 0xf0 && c < 0xf5) v = c & 0x07, k = 4;
    else return (size_t)-1;
    if (i + k > len) return (size_t)-1;
    for (size_t j = 1; j < k; j++) {
      if ((s[i + j] & 0xc0) != 0x80) return (size_t)-1;
      v = v << 6 | (s[i + j] & 0x3f);
    }
    if ((k == 3 && (v < 0x800 || (v >= 0xd800 && v <= 0xdfff))) || (k == 4 && (v < 0x10000 || v > 0x10ffff))) return (size_t)-1;
    out[n++] = v;
    i += k;
  }
  return n;
}

// Checks 1-7 of Section 3 on one term; 0 when valid. cps receives the folded compared form (capacity MAX_U8), *ncp its length.
static int check_term(const pii_term *t, int is_block, uint32_t *cps, size_t *ncp) {
  if (!t->utf8 || (is_block && t->label == -1)) return PII_LIST_ERR_BAD_SHAPE;
  const uint8_t *s = (const uint8_t *)t->utf8;
  if (t->len > MAX_U8) return PII_LIST_ERR_TOO_LONG;
  size_t u16 = 0;  // UTF-16 units without decoding: 1 per non-continuation byte, 2 per lead byte F0-F4
  for (size_t i = 0; i < t->len; i++) u16 += (s[i] & 0xc0) != 0x80 ? (s[i] >= 0xf0 && s[i] <= 0xf4 ? 2 : 1) : 0;
  if (u16 > MAX_U16) return PII_LIST_ERR_TOO_LONG;
  const size_t n = decode_strict(s, t->len, cps);
  if (n == (size_t)-1) return PII_LIST_ERR_BAD_TEXT;
  for (size_t i = 0; i < n; i++)
    if (cps[i] == 0 || line_break(cps[i])) return PII_LIST_ERR_BAD_TEXT;
  if (!n) return PII_LIST_ERR_EMPTY;
  if (n > MAX_CP) return PII_LIST_ERR_TOO_LONG;
  if (white_space(cps[0]) || white_space(cps[n - 1])) return PII_LIST_ERR_EDGE_SPACE;
  if (t->label < (is_block ? 0 : -1) || t->label >= PII_LABEL_COUNT) return PII_LIST_ERR_UNKNOWN_LABEL;
  for (size_t i = 0; i < n; i++) cps[i] = fold(cps[i]);
  *ncp = n;
  return 0;
}

typedef struct {
  const uint32_t *cp;  // compared form
  uint32_t n;          // its length
  uint32_t index;      // original index
  int label;
} entry;

static int cmp_form(const entry *a, const entry *b) {
  const uint32_t m = a->n < b->n ? a->n : b->n;
  for (uint32_t i = 0; i < m; i++)
    if (a->cp[i] != b->cp[i]) return a->cp[i] < b->cp[i] ? -1 : 1;
  return a->n < b->n ? -1 : a->n > b->n;
}
static int cmp_entry(const void *x, const void *y) {
  const entry *a = x, *b = y;
  const int c = cmp_form(a, b);
  return c ? c : a->index < b->index ? -1 : a->index > b->index;
}

// Validates the first MAX_ENTRIES terms in order and copies their compared forms into *buf; *e gets one entry per term.
static int read_list(const pii_term *t, size_t n, int list, entry **e, uint32_t **buf, pii_list_error *err) {
  *e = NULL;
  *buf = NULL;
  const size_t m = n < MAX_ENTRIES ? n : MAX_ENTRIES;
  size_t total = 0;
  for (size_t i = 0; i < m; i++) total += t[i].len < MAX_U8 ? t[i].len : MAX_U8;
  uint32_t tmp[MAX_U8];
  *e = malloc((m ? m : 1) * sizeof **e);
  *buf = malloc((total ? total : 1) * sizeof **buf);
  if (!*e || !*buf) return PII_ERR_NOMEM;
  size_t used = 0;
  for (size_t i = 0; i < m; i++) {
    size_t ncp = 0;
    const int reason = check_term(&t[i], list == PII_LIST_BLOCK, tmp, &ncp);
    if (reason) {
      *err = (pii_list_error){list, i, reason};
      return PII_ERR_ARG;
    }
    memcpy(*buf + used, tmp, ncp * sizeof *tmp);
    (*e)[i] = (entry){*buf + used, (uint32_t)ncp, (uint32_t)i, t[i].label};
    used += ncp;
  }
  if (n > MAX_ENTRIES) {
    *err = (pii_list_error){list, MAX_ENTRIES, PII_LIST_ERR_TOO_MANY};
    return PII_ERR_ARG;
  }
  // forms point into *buf, which does not move after this
  return PII_OK;
}

// Trie of the sorted, distinct forms e[0, n): term k ends at the node of e[k]'s form. Children are contiguous, built level by
// level (each node's children are allocated together).
typedef struct {
  uint32_t node, lo, hi, depth;
} build_item;

static int build_trie(const entry *e, size_t n, pii_trie *t) {
  size_t total = 1;
  for (size_t i = 0; i < n; i++) total += e[i].n;
  t->nodes = malloc(total * sizeof *t->nodes);
  build_item *q = malloc(total * sizeof *q);
  if (!t->nodes || !q) {
    free(q);
    return PII_ERR_NOMEM;
  }
  t->nodes[0] = (pii_node){0, 0, 0, -1};
  size_t count = 1, head = 0, tail = 0;
  q[tail++] = (build_item){0, 0, (uint32_t)n, 0};
  while (head < tail) {
    const build_item it = q[head++];
    uint32_t i = it.lo;
    if (i < it.hi && e[i].n == it.depth) t->nodes[it.node].term = (int32_t)i++;  // forms are distinct: at most one ends here
    const uint32_t first = (uint32_t)count;
    while (i < it.hi) {
      uint32_t j = i + 1;
      while (j < it.hi && e[j].cp[it.depth] == e[i].cp[it.depth]) j++;
      t->nodes[count] = (pii_node){e[i].cp[it.depth], 0, 0, -1};
      q[tail++] = (build_item){(uint32_t)count, i, j, it.depth + 1};
      count++;
      i = j;
    }
    t->nodes[it.node].first = first;
    t->nodes[it.node].n = (uint32_t)count - first;
  }
  t->n = count;
  free(q);
  return PII_OK;
}

static uint32_t child(const pii_trie *t, uint32_t node, uint32_t c) {
  const pii_node *p = t->nodes + t->nodes[node].first;
  size_t lo = 0, hi = t->nodes[node].n;
  while (lo < hi) {
    const size_t mid = (lo + hi) / 2;
    if (p[mid].cp < c) lo = mid + 1;
    else if (p[mid].cp > c) hi = mid;
    else return t->nodes[node].first + (uint32_t)mid;
  }
  return UINT32_MAX;
}

void pii_lists_free(pii_lists *l) {
  if (!l) return;
  free(l->allow.nodes);
  free(l->block.nodes);
  free(l->allow_fam);
  free(l->block_label);
  free(l->block_index);
  free(l);
}

int pii_lists_create(const pii_term *allow, size_t n_allow, const pii_term *block, size_t n_block, pii_lists **out,
                     pii_list_error *err) {
  if (out) *out = NULL;  // before the argument checks: every error leaves *out NULL
  if (!out || !err || (!allow && n_allow) || (!block && n_block)) return PII_ERR_ARG;
  entry *ea = NULL, *eb = NULL;
  uint32_t *ba = NULL, *bb = NULL;
  pii_lists *l = NULL;
  int rc = read_list(allow, n_allow, PII_LIST_ALLOW, &ea, &ba, err);
  if (!rc) rc = read_list(block, n_block, PII_LIST_BLOCK, &eb, &bb, err);
  if (rc) goto done;
  const size_t na = n_allow, nb = n_block;
  qsort(ea, na, sizeof *ea, cmp_entry);
  qsort(eb, nb, sizeof *eb, cmp_entry);
  // label_conflict: the earliest block entry whose label differs from the lowest-index entry of its form
  size_t bad = SIZE_MAX;
  for (size_t i = 0; i < nb;) {
    size_t j = i + 1;
    for (; j < nb && !cmp_form(&eb[i], &eb[j]); j++)
      if (eb[j].label != eb[i].label && eb[j].index < bad) bad = eb[j].index;
    i = j;
  }
  if (bad != SIZE_MAX) {
    *err = (pii_list_error){PII_LIST_BLOCK, bad, PII_LIST_ERR_LABEL_CONFLICT};
    rc = PII_ERR_ARG;
    goto done;
  }
  // in_both: the lowest block index whose form is also in allow
  for (size_t i = 0, j = 0; i < nb; i++) {
    while (j < na && cmp_form(&ea[j], &eb[i]) < 0) j++;
    if (j < na && !cmp_form(&ea[j], &eb[i]) && eb[i].index < bad) bad = eb[i].index;
  }
  if (bad != SIZE_MAX) {
    *err = (pii_list_error){PII_LIST_BLOCK, bad, PII_LIST_ERR_IN_BOTH};
    rc = PII_ERR_ARG;
    goto done;
  }
  // merge duplicates (sorted by form, then index: the first of a run has the lowest index)
  l = calloc(1, sizeof *l);
  if (!l || !(l->allow_fam = malloc((na ? na : 1) * sizeof *l->allow_fam)) ||
      !(l->block_label = malloc((nb ? nb : 1) * sizeof *l->block_label)) ||
      !(l->block_index = malloc((nb ? nb : 1) * sizeof *l->block_index))) {
    rc = PII_ERR_NOMEM;
    goto done;
  }
  size_t k = 0;
  for (size_t i = 0; i < na; k++) {
    uint32_t fam = 0;
    size_t j = i;
    for (; j < na && !cmp_form(&ea[i], &ea[j]); j++) fam |= ea[j].label < 0 ? PII_LABELS_ALL : 1u << family(ea[j].label);
    ea[k] = ea[i];
    l->allow_fam[k] = fam;
    i = j;
  }
  l->n_allow = k;
  k = 0;
  for (size_t i = 0; i < nb; k++) {
    size_t j = i + 1;
    while (j < nb && !cmp_form(&eb[i], &eb[j])) j++;
    eb[k] = eb[i];
    l->block_label[k] = eb[i].label;
    l->block_index[k] = eb[i].index;
    i = j;
  }
  l->n_block = k;
  if ((rc = build_trie(ea, l->n_allow, &l->allow)) || (rc = build_trie(eb, l->n_block, &l->block))) goto done;
  *out = l;
  l = NULL;
done:
  pii_lists_free(l);
  free(ea);
  free(eb);
  free(ba);
  free(bb);
  return rc;
}

void pii_lists_counts(const pii_lists *l, size_t *n_allow, size_t *n_block) {
  if (n_allow) *n_allow = l ? l->n_allow : 0;
  if (n_block) *n_block = l ? l->n_block : 0;
}

int pii_lists_has_allow(const pii_lists *l) { return l && l->n_allow; }
int pii_lists_has_block(const pii_lists *l) { return l && l->n_block; }

int pii_lists_allowed(const pii_lists *l, const uint32_t *cp, size_t a, size_t b, int label) {
  if (!l || !l->n_allow || b <= a || b - a > MAX_CP) return 0;
  uint32_t node = 0;
  for (size_t i = a; i < b; i++)
    if ((node = child(&l->allow, node, fold(cp[i]))) == UINT32_MAX) return 0;
  const int32_t term = l->allow.nodes[node].term;
  return term >= 0 && (l->allow_fam[term] >> family(label) & 1);
}

// Word boundaries inside the search range [a, b) (wordStart / wordEnd of js/src/table.ts with the text cut to the range).
static int starts_word(const uint32_t *cp, size_t a, size_t i) {
  if (i == a || !pii_word_cp(cp[i])) return 1;
  const int h = pii_hangul(cp[i]);
  if (pii_same_word(cp[i - 1], h)) return 0;
  return !(pii_joiner(cp[i - 1]) && i - 1 > a && pii_same_word(cp[i - 2], h));
}
// e ends a word: the range end, a code point after it that does not continue the word, or (Hangul) a rest of the word that
// is particles only. Only pii_max_particle_run + 1 code points are looked at, so the check is bounded.
static int ends_word(const uint32_t *cp, size_t b, size_t e) {
  if (e == b || !pii_word_cp(cp[e - 1])) return 1;
  const int h = pii_hangul(cp[e - 1]);
  const size_t lim = h ? pii_max_particle_run + 1 : 1;
  size_t x = e;
  while (x < b && x - e < lim) {
    if (pii_same_word(cp[x], h) || (pii_joiner(cp[x]) && x + 1 < b && pii_same_word(cp[x + 1], h))) x++;
    else break;
  }
  return x == e || (h && pii_particles_only(cp + e, x - e));
}

int pii_key_less(const pii_cspan *a, const pii_cspan *b) {
  if (a->key_start != b->key_start) return a->key_start < b->key_start;
  if (a->key_len != b->key_len) return a->key_len > b->key_len;
  return a->key_index < b->key_index;
}

int pii_lists_block(const pii_lists *l, const uint32_t *cp, size_t a, size_t b, pii_vec *out) {
  if (!l || !l->n_block) return PII_OK;
  pii_cspan *cur = NULL;
  size_t cur_i = 0;  // index of the current region in out (out may move as it grows)
  for (size_t i = a; i < b; i++) {
    if (!starts_word(cp, a, i)) continue;
    uint32_t node = 0;
    for (size_t k = i; k < b && k - i < MAX_CP; k++) {
      if ((node = child(&l->block, node, fold(cp[k]))) == UINT32_MAX) break;
      const int32_t term = l->block.nodes[node].term;
      if (term < 0 || !ends_word(cp, b, k + 1)) continue;
      const pii_cspan m = {i, k + 1, l->block_label[term], PII_SOURCE_BLOCK, i, (uint32_t)(k + 1 - i), l->block_index[term]};
      cur = cur ? (pii_cspan *)out->p + cur_i : NULL;
      if (cur && i < cur->end) {
        if (m.end > cur->end) cur->end = m.end;
        if (pii_key_less(&m, cur)) {
          cur->label = m.label;
          cur->key_start = m.key_start;
          cur->key_len = m.key_len;
          cur->key_index = m.key_index;
        }
      } else {
        if (!(cur = pii_vec_push(out))) return PII_ERR_NOMEM;
        *cur = m;
        cur_i = out->n - 1;
      }
    }
  }
  return PII_OK;
}

#ifdef PII_TEST_LAYOUT
// Test build only (flutter/test/layout_test.dart): offsetof of each field of pii_options and pii_table_options, by field
// index in declaration order; SIZE_MAX past the last field. Not in pii.h.
PII_API size_t pii_test_options_offset(int field);
PII_API size_t pii_test_table_options_offset(int field);

size_t pii_test_options_offset(int field) {
  static const size_t off[] = {
      offsetof(pii_options, struct_size), offsetof(pii_options, sensitivity), offsetof(pii_options, labels),
      offsetof(pii_options, extra),       offsetof(pii_options, n_extra),     offsetof(pii_options, extra_unit),
      offsetof(pii_options, keep_emoji),  offsetof(pii_options, whole_words), offsetof(pii_options, strict_short_names),
      offsetof(pii_options, lists),
  };
  return field >= 0 && (size_t)field < sizeof off / sizeof *off ? off[field] : SIZE_MAX;
}

size_t pii_test_table_options_offset(int field) {
  static const size_t off[] = {
      offsetof(pii_table_options, struct_size), offsetof(pii_table_options, delimiter), offsetof(pii_table_options, header),
      offsetof(pii_table_options, vote),        offsetof(pii_table_options, context),
  };
  return field >= 0 && (size_t)field < sizeof off / sizeof *off ? off[field] : SIZE_MAX;
}
#endif
