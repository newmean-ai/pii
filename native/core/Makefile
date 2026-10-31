# Plain build of the core: static and shared library, the test CLI, and a sanitizer build of the CLI.
#   make            build/libpii.a, build/libpii.{so,dylib}, build/pii_cli
#   make FMA=1      the same with fused multiply-add in the matrix kernel (like the relaxed-SIMD wasm build; x86-64 needs FMA3)
#   make asan       build/asan/pii_cli (AddressSanitizer + UndefinedBehaviorSanitizer)
#   make test       python3 tests/run_tests.py (fixture parity; skips without fixtures)
# -ffp-contract=off keeps every other multiply and add separate, as in the WebAssembly engine; required for identical results.
# -fno-math-errno: sqrt compiles to one instruction without a libm call (no -lm needed; results unchanged).
CC ?= cc
OPT ?= -O3
CFLAGS ?=
BASE = -std=c11 -Wall -Wextra -Wshadow -Wstrict-prototypes -ffp-contract=off -fno-math-errno -fvisibility=hidden -fPIC
ifeq ($(FMA),1)
BASE += -DPII_FMA $(if $(filter x86_64 amd64,$(shell uname -m)),-mfma)
endif
SRC = src/api.c src/engine.c src/onnx.c src/tokenizer.c src/util.c src/sha256.c src/models.c
HDR = pii.h src/internal.h src/simd.h src/unicode_tables.h
B = build
UNAME := $(shell uname -s)
SHLIB = $(B)/libpii.$(if $(filter Darwin,$(UNAME)),dylib,so)
CLI_DEFS = -D_POSIX_C_SOURCE=200809L

all: $(B)/libpii.a $(SHLIB) $(B)/pii_cli

$(B)/%.o: src/%.c $(HDR)
	@mkdir -p $(B)
	$(CC) $(BASE) $(OPT) $(CFLAGS) -c $< -o $@

$(B)/libpii.a: $(SRC:src/%.c=$(B)/%.o)
	rm -f $@
	ar rcs $@ $^

$(SHLIB): $(SRC:src/%.c=$(B)/%.o)
	$(CC) -shared $(OPT) $(CFLAGS) -o $@ $^

$(B)/pii_cli: tests/pii_cli.c $(B)/libpii.a
	$(CC) $(BASE) $(OPT) $(CFLAGS) $(CLI_DEFS) tests/pii_cli.c $(B)/libpii.a -lpthread -o $@

asan: $(B)/asan/pii_cli
$(B)/asan/pii_cli: tests/pii_cli.c $(SRC) $(HDR)
	@mkdir -p $(B)/asan
	$(CC) $(BASE) -O1 -g -fno-omit-frame-pointer -fsanitize=address,undefined -fno-sanitize-recover=undefined $(CFLAGS) \
	  $(CLI_DEFS) tests/pii_cli.c $(SRC) -lpthread -o $@

test: all asan
	python3 tests/run_tests.py

clean:
	rm -rf $(B)

.PHONY: all asan test clean
