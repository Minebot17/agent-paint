"""Independent painting engine: no native desktop dependencies."""
import os

# numpy's bundled OpenBLAS commits one work buffer per CPU thread when it is imported (about 480 MB
# of private memory on a 16-thread machine). The engine does no heavy linear algebra, so one BLAS
# thread is enough. This runs before any submodule imports numpy; an explicit setting still wins.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

__version__ = "0.3.0"
