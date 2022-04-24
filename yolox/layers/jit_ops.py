#!/usr/bin/env python3
# Copyright (c) Megvii, Inc. and its affiliates. All Rights Reserved

import glob
import importlib
import os
import sys
import time
from typing import List

__all__ = ["JitOp", "FastCOCOEvalOp"]


class JitOp:
    """
    Just-in-time compilation of ops.

    Some code of `JitOp` is inspired by `deepspeed.op_builder`,
    check the following link for more details:
    https://github.com/microsoft/DeepSpeed/blob/master/op_builder/builder.py
    """

    def __init__(self, name):
        self.name = name

    def absolute_name(self) -> str:
        """Get absolute build path for cases where the op is pre-installed."""
        pass

    def sources(self) -> List:
        """Get path list of source files of op.

        NOTE: the path should be elative to root of package during building,
            Otherwise, exception will be raised when building package.
            However, for runtime building, path will be absolute.
        """
        pass

    def include_dirs(self) -> List:
        """
        Get list of include paths, relative to root of package.

        NOTE: the path should be elative to root of package.
            Otherwise, exception will be raised when building package.
        """
        return []

    def define_macros(self) -> List:
        """Get list of macros to define for op"""
        return []

    def cxx_args(self) -> List:
        """Get optional list of compiler flags to forward"""
        args = ["-O2"] if sys.platform == "win32" else ["-O3", "-std=c++14", "-g", "-Wno-reorder"]
        return args

    def nvcc_args(self) -> List:
        """Get optional list of compiler flags to forward to nvcc when building CUDA sources"""
        args = [
            "-O3", "--use_fast_math",
            "-std=c++17" if sys.platform == "win32" else "-std=c++14",
            "-U__CUDA_NO_HALF_OPERATORS__",
            "-U__CUDA_NO_HALF_CONVERSIONS__",
            "-U__CUDA_NO_HALF2_OPERATORS__",
        ]
        return args

    def build_op(self):
        from torch.utils.cpp_extension import CppExtension
        return CppExtension(
            name=self.absolute_name(),
            sources=self.sources(),
            include_dirs=self.include_dirs(),
            define_macros=self.define_macros(),
            extra_compile_args={
                "cxx": self.cxx_args(),
            },
        )

    def load(self, verbose=True):
        try:
            # try to import op from pre-installed package
            return importlib.import_module(self.absolute_name())
        except Exception:  # op not compiled, jit load
            from yolox.utils import wait_for_the_master
            with wait_for_the_master():  # to avoid race condition
                return self.jit_load(verbose)

    def jit_load(self, verbose=True):
        from torch.utils.cpp_extension import load
        from loguru import logger
        try:
            import ninja  # noqa
        except ImportError:
            if verbose:
                logger.warning(
                    f"Ninja is not installed, fall back to normal installation for {self.name}."
                )

        build_tik = time.time()
        # build op and load
        op_module = load(
            name=self.name,
            sources=self.sources(),
            extra_cflags=self.cxx_args(),
            extra_cuda_cflags=self.nvcc_args(),
            verbose=verbose,
        )
        build_duration = time.time() - build_tik
        if verbose:
            logger.info(f"Load {self.name} op in {build_duration:.3f}s.")
        return op_module

    def clear_dynamic_library(self):
        """Remove dynamic libraray files generated by JIT compilation."""
        module = self.load()
        os.remove(module.__file__)


class FastCOCOEvalOp(JitOp):

    def __init__(self, name="fast_cocoeval"):
    # def __init__(self, name="fast_coco_eval"):
        super().__init__(name=name)

    def absolute_name(self):
        return f'yolox.layers.{self.name}'

    def sources(self):
        sources = glob.glob(os.path.join("yolox", "layers", "cocoeval", "*.cpp"))
        if not sources:  # source will be empty list if the so file is removed after install
            # use abosolute path to compile
            import yolox
            code_path = os.path.join(yolox.__path__[0], "layers", "cocoeval", "*.cpp")
            sources = glob.glob(code_path)
        return sources

    def include_dirs(self):
        return [os.path.join("yolox", "layers", "cocoeval")]
