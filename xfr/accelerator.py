"""Abstract base class for PRU ICSSG broadside accelerators.

Each accelerator has a unique XFR device ID and is accessed via
XIN, XOUT, and XCHG instructions from PRUCore.
"""

from abc import ABC, abstractmethod


class Accelerator(ABC):
    """Interface all broadside accelerators must implement."""

    DEVICE_ID: int  # Override in each subclass

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if not isinstance(getattr(cls, "DEVICE_ID", None), int):
            raise TypeError(f"{cls.__name__} must define DEVICE_ID as an int class attribute")

    @abstractmethod
    def xout(self, start_reg: int, data: bytes) -> None:
        """Handle an XOUT instruction targeting this accelerator.

        Args:
            start_reg: The PRU register index the XOUT started from.
            data: The raw bytes read from PRU registers (length as specified in instruction).
        """

    @abstractmethod
    def xin(self, start_reg: int, length: int) -> bytes:
        """Handle an XIN instruction targeting this accelerator.

        Args:
            start_reg: The PRU register index to write into.
            length: Number of bytes requested.

        Returns:
            Exactly `length` bytes to write into PRU registers starting at start_reg.
        """

    @abstractmethod
    def xchg(self, start_reg: int, data: bytes) -> bytes:
        """Handle an XCHG instruction targeting this accelerator.

        Args:
            start_reg: Starting PRU register index.
            data: Bytes from PRU registers (same as xout).

        Returns:
            Bytes to write back into PRU registers (same as xin).
        """

    @abstractmethod
    def reset(self) -> None:
        """Reset accelerator state to power-on defaults."""
