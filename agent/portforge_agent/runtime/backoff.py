"""Exponential backoff with jitter for network synchronization."""

import random
import time
from typing import Optional


class ExponentialBackoff:
    def __init__(self, initial: float = 1.0, max_delay: float = 60.0, factor: float = 2.0, jitter: float = 0.2):
        self.initial = initial
        self.max_delay = max_delay
        self.factor = factor
        self.jitter = jitter
        self.current_delay: Optional[float] = None

    def next_delay(self) -> float:
        """Computes the next delay and advances internal state."""
        if self.current_delay is None:
            self.current_delay = self.initial
        else:
            self.current_delay = min(self.max_delay, self.current_delay * self.factor)
            
        # Add jitter
        jitter_range = self.current_delay * self.jitter
        delay = self.current_delay + random.uniform(-jitter_range, jitter_range)
        return min(self.max_delay, max(0.1, delay))
        
    def reset(self) -> None:
        """Resets the backoff after a successful operation."""
        self.current_delay = None

    def sleep(self) -> None:
        """Sleeps for the next calculated delay."""
        time.sleep(self.next_delay())
