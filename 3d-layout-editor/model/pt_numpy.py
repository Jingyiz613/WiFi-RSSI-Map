"""Read a torch.save checkpoint without importing torch.

The checkpoint is a zip with data.pkl + raw float32 storages. We only need the
plain config dict and the float32 tensors, so a minimal unpickler is enough.
This keeps the demo runnable on machines where torch's DLLs are blocked.
"""
from __future__ import annotations

import io
import pickle
import zipfile
from pathlib import Path

import numpy as np


class _TorchPlaceholder:
    """Stand-in for torch classes referenced by the pickle."""


class _Storage:
    __slots__ = ("key", "array")

    def __init__(self, key: str, array: np.ndarray) -> None:
        self.key = key
        self.array = array


class _Tensor:
    __slots__ = ("storage", "offset", "size", "stride")

    def __init__(self, storage: _Storage, offset: int, size, stride) -> None:
        self.storage = storage
        self.offset = int(offset)
        self.size = tuple(int(v) for v in size)
        self.stride = tuple(int(v) for v in stride)

    def numpy(self) -> np.ndarray:
        base = self.storage.array[self.offset :]
        view = np.lib.stride_tricks.as_strided(
            base, shape=self.size, strides=[s * 4 for s in self.stride]
        )
        return np.array(view, dtype=np.float32, copy=True)


def _rebuild_tensor_v2(storage, storage_offset, size, stride, *args, **kwargs):
    return _Tensor(storage, storage_offset, size, stride)


def load_checkpoint(path: str | Path) -> tuple[dict, dict[str, np.ndarray]]:
    with zipfile.ZipFile(path) as zf:
        pkl_name = next(n for n in zf.namelist() if n.endswith("data.pkl"))
        prefix = pkl_name[: -len("data.pkl")]

        class Loader(pickle.Unpickler):
            def find_class(self, module, name):
                if module == "torch._utils" and name == "_rebuild_tensor_v2":
                    return _rebuild_tensor_v2
                if module.startswith("torch"):
                    return _TorchPlaceholder
                return super().find_class(module, name)

            def persistent_load(self, pid):
                if pid and pid[0] == "storage":
                    _, _, key, _, _numel = pid
                    raw = zf.read(f"{prefix}data/{key}")
                    array = np.frombuffer(raw, dtype="<f4").copy()
                    return _Storage(str(key), array)
                raise pickle.UnpicklingError(f"unsupported persistent id: {pid!r}")

        obj = Loader(io.BytesIO(zf.read(pkl_name))).load()

    config = dict(obj.get("config") or {})
    state = obj.get("model_state_dict") or {}
    weights = {key: value.numpy() for key, value in state.items()}
    return config, weights
