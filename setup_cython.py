"""Build Cython extensions in-place.

Usage:
    python setup_cython.py build_ext --inplace
"""

from setuptools import Extension, setup

try:
    from Cython.Build import cythonize
except ImportError as exc:  # pragma: no cover - setup-time guard
    raise SystemExit(
        "Cython is required to build extensions. Install it with: "
        "python -m pip install Cython"
    ) from exc


extensions = [
    Extension(
        "backend.engine.snapshot_cy",
        ["backend/engine/snapshot_cy.pyx"],
    )
]


setup(
    name="roco-cython-extensions",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            "language_level": "3",
            "boundscheck": False,
            "wraparound": False,
            "cdivision": True,
            "embedsignature": True,
        },
        annotate=False,
    ),
)
