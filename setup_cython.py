"""Setup script for compiling Cython extensions"""

from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy as np

extensions = [
    Extension(
        "backend.engine.snapshot_cy",
        ["backend/engine/snapshot_cy.pyx"],
        include_dirs=[np.get_include()],
        extra_compile_args=["/O2"] if __import__("sys").platform == "win32" else ["-O3"],
    )
]

setup(
    name="roco-cython-extensions",
    ext_modules=cythonize(
        extensions,
        compiler_directives={
            'language_level': "3",
            'boundscheck': False,
            'wraparound': False,
            'cdivision': True,
            'embedsignature': True,
        },
        annotate=True,  # 生成 HTML 分析文件
    ),
)
