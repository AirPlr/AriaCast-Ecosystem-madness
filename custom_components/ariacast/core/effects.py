"""RoomEffectsChain: a per-room audio effects chain — 3-band EQ, a light
Schroeder-style reverb, and an RMS soft-knee compressor/limiter — applied
once to the raw inbound PCM mix in `AriaCastSocketServer._fan_out`, before
it's distributed to each speaker's own gain/delay pipeline. Room-level tone
shaping happens once here; per-speaker spatial adjustment stays downstream
in `socket_server.py` as before.

Deliberately numpy-only, no scipy: `scipy.signal.lfilter` would make the
per-sample IIR recursion trivial, but scipy is a much heavier binary wheel
than numpy, and this project already got burned once by a binary-dependency/
CPU-model mismatch (see the numpy note in main.py's import handler). The
per-sample recursion below runs in plain Python floats instead of numpy
scalars — numpy's per-call dispatch overhead (a few microseconds) would
dominate at this array size (2-element channel vectors) and blow the 20ms
real-time budget; plain Python arithmetic in a tight loop is materially
faster here despite "no numpy" sounding backwards for DSP code.

Every stage has a true bypass fast path (flat EQ / zero reverb wet / no
compression all skip their processing entirely), so a room using none of
this — the default for every existing room — costs nothing extra.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

SAMPLE_RATE = 48000
CHANNELS = 2

_EQ_EPSILON_DB = 0.05  # below this, a band is treated as flat (identity, no filtering cost)


def _peaking_coeffs(freq_hz: float, gain_db: float, q: float) -> tuple[float, float, float, float, float]:
    """RBJ Audio EQ Cookbook peaking-EQ biquad coefficients (b0,b1,b2,a1,a2, already normalized by a0)."""
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * freq_hz / SAMPLE_RATE
    alpha = math.sin(w0) / (2 * q)
    cos_w0 = math.cos(w0)
    b0, b1, b2 = 1 + alpha * A, -2 * cos_w0, 1 - alpha * A
    a0, a1, a2 = 1 + alpha / A, -2 * cos_w0, 1 - alpha / A
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


def _shelf_coeffs(freq_hz: float, gain_db: float, q: float, low: bool) -> tuple[float, float, float, float, float]:
    """RBJ low-shelf / high-shelf biquad coefficients."""
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * freq_hz / SAMPLE_RATE
    alpha = math.sin(w0) / 2 * math.sqrt((A + 1 / A) * (1 / q - 1) + 2)
    cos_w0 = math.cos(w0)
    sqrtA = math.sqrt(A)
    if low:
        b0 = A * ((A + 1) - (A - 1) * cos_w0 + 2 * sqrtA * alpha)
        b1 = 2 * A * ((A - 1) - (A + 1) * cos_w0)
        b2 = A * ((A + 1) - (A - 1) * cos_w0 - 2 * sqrtA * alpha)
        a0 = (A + 1) + (A - 1) * cos_w0 + 2 * sqrtA * alpha
        a1 = -2 * ((A - 1) + (A + 1) * cos_w0)
        a2 = (A + 1) + (A - 1) * cos_w0 - 2 * sqrtA * alpha
    else:
        b0 = A * ((A + 1) + (A - 1) * cos_w0 + 2 * sqrtA * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cos_w0)
        b2 = A * ((A + 1) + (A - 1) * cos_w0 - 2 * sqrtA * alpha)
        a0 = (A + 1) - (A - 1) * cos_w0 + 2 * sqrtA * alpha
        a1 = 2 * ((A - 1) - (A + 1) * cos_w0)
        a2 = (A + 1) - (A - 1) * cos_w0 - 2 * sqrtA * alpha
    return b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0


# Band centers: a low shelf for "bass", a peaking bell for "mid", a high
# shelf for "treble" — the standard 3-band tone-control layout.
EQ_BASS_HZ = 120.0
EQ_MID_HZ = 1000.0
EQ_TREBLE_HZ = 6000.0


class OnePoleLowpass:
    """Cheap 6dB/oct one-pole lowpass — used for the distance-based
    air-absorption cue in socket_server.py (real hardware/geometry
    justification: sound loses high frequencies over distance through air).
    Much cheaper than a full biquad, plenty convincing for a subtle cue."""

    __slots__ = ("_coeff", "_y_l", "_y_r")

    def __init__(self) -> None:
        self._coeff = 0.0  # 0 = bypass
        self._y_l = 0.0
        self._y_r = 0.0

    def set_cutoff(self, cutoff_hz: Optional[float]) -> None:
        if cutoff_hz is None or cutoff_hz >= SAMPLE_RATE / 2:
            self._coeff = 0.0
            return
        rc = 1.0 / (2 * math.pi * max(20.0, cutoff_hz))
        dt = 1.0 / SAMPLE_RATE
        self._coeff = dt / (rc + dt)

    def process(self, samples_i16: np.ndarray) -> np.ndarray:
        coeff = self._coeff
        if coeff <= 0.0:
            return samples_i16
        stereo = samples_i16.reshape(-1, CHANNELS).astype(np.float64)
        n = stereo.shape[0]
        out = np.empty_like(stereo)
        y_l, y_r = self._y_l, self._y_r
        col_l, col_r = stereo[:, 0], stereo[:, 1]
        for i in range(n):
            y_l += coeff * (col_l[i] - y_l)
            y_r += coeff * (col_r[i] - y_r)
            out[i, 0] = y_l
            out[i, 1] = y_r
        self._y_l, self._y_r = float(y_l), float(y_r)
        return np.clip(out, -32768, 32767).astype("<i2").reshape(-1)


@dataclass
class EQSettings:
    bass_db: float = 0.0
    mid_db: float = 0.0
    treble_db: float = 0.0

    @property
    def is_flat(self) -> bool:
        return (
            abs(self.bass_db) < _EQ_EPSILON_DB
            and abs(self.mid_db) < _EQ_EPSILON_DB
            and abs(self.treble_db) < _EQ_EPSILON_DB
        )


@dataclass
class ReverbSettings:
    wet: float = 0.0  # 0..1, 0 = fully bypassed
    size: float = 0.5  # 0..1, scales comb delay lengths / feedback

    @property
    def is_bypassed(self) -> bool:
        return self.wet <= 0.0


@dataclass
class CompressorSettings:
    enabled: bool = False
    threshold_db: float = -18.0
    ratio: float = 3.0
    attack_ms: float = 8.0
    release_ms: float = 200.0
    makeup_db: float = 0.0


# Comb-filter delays in ms, roughly Schroeder/Freeverb-style spacing so they
# don't reinforce each other's resonances. Scaled by ReverbSettings.size at
# runtime (0.5 = these values as-is).
_COMB_BASE_MS = (29.7, 37.1, 41.1)
_COMB_FEEDBACK = 0.78
_ALLPASS_MS = 5.0
_ALLPASS_GAIN = 0.5


# One-click bundles of the chain above. Values are opinionated but modest —
# each is a starting point meant to be nudged from the Web UI sliders, not a
# "correct" mix for every room/speaker combination.
EFFECTS_PRESETS: dict[str, dict] = {
    "flat": {
        "eq_bass_db": 0.0, "eq_mid_db": 0.0, "eq_treble_db": 0.0,
        "reverb_wet": 0.0, "reverb_size": 0.5, "compressor_enabled": False,
    },
    "music": {
        "eq_bass_db": 3.0, "eq_mid_db": -1.0, "eq_treble_db": 2.0,
        "reverb_wet": 0.12, "reverb_size": 0.4, "compressor_enabled": True,
    },
    "movie": {
        "eq_bass_db": 4.0, "eq_mid_db": -2.0, "eq_treble_db": 1.0,
        "reverb_wet": 0.2, "reverb_size": 0.6, "compressor_enabled": True,
    },
    "voice": {
        "eq_bass_db": -6.0, "eq_mid_db": 4.0, "eq_treble_db": 2.0,
        "reverb_wet": 0.0, "reverb_size": 0.5, "compressor_enabled": True,
    },
    "night": {
        "eq_bass_db": -8.0, "eq_mid_db": 0.0, "eq_treble_db": -3.0,
        "reverb_wet": 0.0, "reverb_size": 0.5, "compressor_enabled": True,
    },
}


class RoomEffectsChain:
    """Owns all filter/reverb/compressor state for one room. `process()`
    runs the whole chain on one 20ms stereo int16 frame and returns the
    processed frame, same size and format."""

    def __init__(self) -> None:
        self.eq = EQSettings()
        self.reverb = ReverbSettings()
        self.compressor = CompressorSettings()

        # EQ biquad coefficients (recomputed only when settings change).
        self._eq_coeffs: list[tuple[float, float, float, float, float]] = []
        # Per-channel biquad delay state: [(x1,x2,y1,y2) per band] per channel.
        self._eq_state_l = [[0.0, 0.0, 0.0, 0.0] for _ in range(3)]
        self._eq_state_r = [[0.0, 0.0, 0.0, 0.0] for _ in range(3)]

        # Reverb comb-filter circular buffers + feedback lowpass state.
        self._comb_bufs_l: list[deque] = []
        self._comb_bufs_r: list[deque] = []
        self._comb_damp_l = [0.0, 0.0, 0.0]
        self._comb_damp_r = [0.0, 0.0, 0.0]
        self._allpass_buf_l: deque = deque()
        self._allpass_buf_r: deque = deque()
        self._reverb_size_built = -1.0

        # Compressor envelope (smoothed gain reduction, in linear scale).
        self._comp_envelope_db = 0.0

        self.set_eq(EQSettings())
        self.set_reverb(ReverbSettings())

    # -- configuration ----------------------------------------------------

    def set_eq(self, eq: EQSettings) -> None:
        self.eq = eq
        if eq.is_flat:
            self._eq_coeffs = []
            return
        self._eq_coeffs = [
            _shelf_coeffs(EQ_BASS_HZ, eq.bass_db, 0.9, low=True) if abs(eq.bass_db) >= _EQ_EPSILON_DB else None,
            _peaking_coeffs(EQ_MID_HZ, eq.mid_db, 1.0) if abs(eq.mid_db) >= _EQ_EPSILON_DB else None,
            _shelf_coeffs(EQ_TREBLE_HZ, eq.treble_db, 0.9, low=False) if abs(eq.treble_db) >= _EQ_EPSILON_DB else None,
        ]

    def set_reverb(self, reverb: ReverbSettings) -> None:
        self.reverb = reverb
        if reverb.is_bypassed:
            return
        size = max(0.05, min(1.0, reverb.size))
        if size == self._reverb_size_built:
            return
        self._reverb_size_built = size
        delay_samples = [int(ms * size * 2 * SAMPLE_RATE / 1000.0) or 1 for ms in _COMB_BASE_MS]
        self._comb_bufs_l = [deque([0.0] * n, maxlen=n) for n in delay_samples]
        self._comb_bufs_r = [deque([0.0] * n, maxlen=n) for n in delay_samples]
        ap_n = max(1, int(_ALLPASS_MS * SAMPLE_RATE / 1000.0))
        self._allpass_buf_l = deque([0.0] * ap_n, maxlen=ap_n)
        self._allpass_buf_r = deque([0.0] * ap_n, maxlen=ap_n)

    def set_compressor(self, comp: CompressorSettings) -> None:
        self.compressor = comp

    @property
    def is_fully_bypassed(self) -> bool:
        return self.eq.is_flat and self.reverb.is_bypassed and not self.compressor.enabled

    # -- processing ---------------------------------------------------------

    def process(self, frame: bytes) -> bytes:
        if self.is_fully_bypassed:
            return frame

        samples = np.frombuffer(frame, dtype="<i2")
        stereo = samples.reshape(-1, CHANNELS).astype(np.float64)
        left = stereo[:, 0].tolist()
        right = stereo[:, 1].tolist()

        if self._eq_coeffs:
            left, right = self._apply_eq(left, right)
        if not self.reverb.is_bypassed:
            left, right = self._apply_reverb(left, right)

        out = np.empty((len(left), CHANNELS), dtype=np.float64)
        out[:, 0] = left
        out[:, 1] = right

        if self.compressor.enabled:
            out = self._apply_compressor(out)

        return np.clip(out, -32768, 32767).astype("<i2").reshape(-1).tobytes()

    def _apply_eq(self, left: list, right: list) -> tuple[list, list]:
        coeffs = self._eq_coeffs
        state_l, state_r = self._eq_state_l, self._eq_state_r
        n = len(left)
        for band_idx, c in enumerate(coeffs):
            if c is None:
                continue
            b0, b1, b2, a1, a2 = c
            sl, sr = state_l[band_idx], state_r[band_idx]
            x1l, x2l, y1l, y2l = sl
            x1r, x2r, y1r, y2r = sr
            for i in range(n):
                x0l = left[i]
                y0l = b0 * x0l + b1 * x1l + b2 * x2l - a1 * y1l - a2 * y2l
                left[i] = y0l
                x2l, x1l, y2l, y1l = x1l, x0l, y1l, y0l

                x0r = right[i]
                y0r = b0 * x0r + b1 * x1r + b2 * x2r - a1 * y1r - a2 * y2r
                right[i] = y0r
                x2r, x1r, y2r, y1r = x1r, x0r, y1r, y0r
            state_l[band_idx] = [x1l, x2l, y1l, y2l]
            state_r[band_idx] = [x1r, x2r, y1r, y2r]
        return left, right

    def _apply_reverb(self, left: list, right: list) -> tuple[list, list]:
        wet = max(0.0, min(1.0, self.reverb.wet))
        dry = 1.0 - wet
        n = len(left)
        out_l = [0.0] * n
        out_r = [0.0] * n

        combs_l, combs_r = self._comb_bufs_l, self._comb_bufs_r
        damp_l, damp_r = self._comb_damp_l, self._comb_damp_r
        fb = _COMB_FEEDBACK
        n_combs = len(combs_l)

        for i in range(n):
            xl, xr = left[i], right[i]
            wet_l = 0.0
            wet_r = 0.0
            for ci in range(n_combs):
                bl, br = combs_l[ci], combs_r[ci]
                yl = bl[0]
                yr = br[0]
                damp_l[ci] = yl * 0.2 + damp_l[ci] * 0.8
                damp_r[ci] = yr * 0.2 + damp_r[ci] * 0.8
                bl.append(xl + damp_l[ci] * fb)
                br.append(xr + damp_r[ci] * fb)
                wet_l += yl
                wet_r += yr
            wet_l /= n_combs
            wet_r /= n_combs

            apl = self._allpass_buf_l
            apr = self._allpass_buf_r
            bufl = apl[0]
            bufr = apr[0]
            out_apl = -wet_l * _ALLPASS_GAIN + bufl
            out_apr = -wet_r * _ALLPASS_GAIN + bufr
            apl.append(wet_l + out_apl * _ALLPASS_GAIN)
            apr.append(wet_r + out_apr * _ALLPASS_GAIN)

            out_l[i] = xl * dry + out_apl * wet
            out_r[i] = xr * dry + out_apr * wet

        return out_l, out_r

    def _apply_compressor(self, stereo: np.ndarray) -> np.ndarray:
        comp = self.compressor
        n = stereo.shape[0]
        block_rms = float(np.sqrt(np.mean(stereo ** 2)) + 1e-6)
        block_db = 20.0 * math.log10(block_rms / 32768.0 + 1e-9)

        over_db = block_db - comp.threshold_db
        target_reduction_db = max(0.0, over_db - over_db / comp.ratio) if over_db > 0 else 0.0

        frame_ms = n / SAMPLE_RATE * 1000.0
        coeff = frame_ms / max(1.0, comp.attack_ms if target_reduction_db > self._comp_envelope_db else comp.release_ms)
        coeff = max(0.0, min(1.0, coeff))
        self._comp_envelope_db += (target_reduction_db - self._comp_envelope_db) * coeff

        gain_db = comp.makeup_db - self._comp_envelope_db
        gain_linear = 10 ** (gain_db / 20.0)
        return stereo * gain_linear
