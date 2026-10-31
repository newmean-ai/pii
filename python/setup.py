# Builds newmean_pii._core from the native core (native/core in the repository; native-core/ inside the sdist, copied
# there by the sdist command below so the sdist builds on its own; the sources are not duplicated in git).
import os
import shutil
import sysconfig
from pathlib import Path

from setuptools import Extension, setup
from setuptools.command.build_ext import build_ext
from setuptools.command.sdist import sdist

HERE = Path(__file__).resolve().parent
VENDORED = HERE / "native-core"
CORE = VENDORED if VENDORED.is_dir() else HERE.parent / "native" / "core"
CORE_FILES = ["pii.h", "LICENSE", "NOTICE"] + [f"src/{n}" for n in (
    "api.c", "engine.c", "onnx.c", "tokenizer.c", "util.c", "sha256.c", "models.c", "internal.h", "simd.h", "unicode_tables.h",
)]
if not (CORE / "pii.h").is_file():
    raise SystemExit(f"native core not found at {CORE}: build from the repository or from the sdist")

FREE_THREADED = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))  # no limited API there
LIMITED = not FREE_THREADED


def rel(p: Path) -> str:
    return os.path.relpath(p, HERE).replace(os.sep, "/")


class BuildExt(build_ext):
    # Same flags as native/core/Makefile and CMakeLists.txt: -ffp-contract=off (MSVC /fp:precise) keeps every multiply
    # and add separate, as in the npm package's WebAssembly engine; required for its bit-identical logits.
    def build_extensions(self):
        if self.compiler.compiler_type == "msvc":
            args = ["/std:c11", "/O2", "/fp:precise"]
        else:
            args = ["-std=c11", "-O3", "-fno-wrapv", "-ffp-contract=off", "-fno-math-errno", "-fvisibility=hidden"]
        for ext in self.extensions:
            ext.extra_compile_args = args + ext.extra_compile_args
        super().build_extensions()


class Sdist(sdist):
    def make_release_tree(self, base_dir, files):
        super().make_release_tree(base_dir, [f for f in files if not f.startswith("..")])
        for name in CORE_FILES:
            dest = Path(base_dir) / "native-core" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(CORE / name, dest)


ext = Extension(
    "newmean_pii._core",
    sources=["src/newmean_pii/_core.c"] + [rel(CORE / f) for f in CORE_FILES if f.endswith(".c")],
    include_dirs=[rel(CORE), rel(CORE / "src")],
    define_macros=[("Py_LIMITED_API", "0x03090000")] if LIMITED else [],
    py_limited_api=LIMITED,
)

setup(
    ext_modules=[ext],
    cmdclass={"build_ext": BuildExt, "sdist": Sdist},
    options={"bdist_wheel": {"py_limited_api": "cp39"}} if LIMITED else {},
)
