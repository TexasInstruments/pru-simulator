"""32-byte scratchpad shared storage used by the XFR bus."""


class Scratchpad:
    """Shared scratchpad with atomic exchange support (variable size)."""

    def __init__(self, size: int = 32):
        self.data = bytearray(size)
        self._size = size

    def _check_bounds(self, offset: int, length: int) -> None:
        if offset < 0 or (offset + length) > self._size:
            raise ValueError(
                f"Scratchpad access out of bounds: offset={offset}, length={length}"
            )

    def read(self, offset: int, length: int) -> bytes:
        """Read *length* bytes starting at *offset*."""
        self._check_bounds(offset, length)
        return bytes(self.data[offset : offset + length])

    def write(self, offset: int, data: bytes) -> None:
        """Write *data* starting at *offset*."""
        self._check_bounds(offset, len(data))
        self.data[offset : offset + len(data)] = data

    def exchange(self, offset: int, data: bytes) -> bytes:
        """Atomically swap *data* into scratchpad, returning the old contents."""
        self._check_bounds(offset, len(data))
        old = bytes(self.data[offset : offset + len(data)])
        self.data[offset : offset + len(data)] = data
        return old
