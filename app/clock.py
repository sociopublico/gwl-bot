from __future__ import annotations


def pcm_seconds(n_bytes: int, sample_rate: int) -> float:
    if sample_rate <= 0:
        raise ValueError("sample_rate debe ser mayor a 0")
    return n_bytes / (sample_rate * 2)


class StreamClock:
    """Reloj de video: origin + segundos de PCM fresco (sin contar overlap)."""

    def __init__(self, origin_seconds: float = 0.0, sample_rate: int = 16000) -> None:
        if origin_seconds < 0:
            raise ValueError("origin_seconds no puede ser negativo")
        if sample_rate <= 0:
            raise ValueError("sample_rate debe ser mayor a 0")
        self.origin_seconds = origin_seconds
        self.sample_rate = sample_rate
        self.fresh_bytes = 0

    def add_fresh(self, n_bytes: int) -> None:
        if n_bytes < 0:
            raise ValueError("n_bytes no puede ser negativo")
        self.fresh_bytes += n_bytes

    @property
    def fresh_seconds(self) -> float:
        return pcm_seconds(self.fresh_bytes, self.sample_rate)

    def window_start(self, window_bytes: int) -> float:
        duration = pcm_seconds(window_bytes, self.sample_rate)
        return self.origin_seconds + self.fresh_seconds - duration

    def video_seconds(self, window_bytes: int, segment_start: float) -> float:
        return max(0.0, self.window_start(window_bytes) + segment_start)
