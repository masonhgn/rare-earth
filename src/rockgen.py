
# procedural rock-patch shapes, kept separate from rendering so it stays
# headless-safe (numpy only, no pygame/display). a rock patch is an organic
# *mask* (a lumpy blob), NOT a set of tiles: the renderer composites a rock
# texture through it, so the rock/grass boundary is a smooth curve with zero
# edge/corner cases. the gameplay grid is a lossy projection of the mask,
# sampled per tile by tile_coverage().
#
# the shape is domain-warped fractal value noise bounded by a radial falloff:
#   * value noise         — coherent (not white) noise on a lattice, so the
#                           boundary undulates instead of sparkling.
#   * fBm (octaves)       — summed at doubling frequency / halving amplitude for
#                           self-similar detail: big lumps down to fine frays.
#   * domain warp         — the sample coords are pushed around by a second fBm,
#                           which bends the round blob into geological shapes.
#   * radial falloff      — distance-from-center is subtracted so the patch is
#                           bounded and frays into grass rather than clipping to
#                           its bounding box.
#
# EVERYTHING is defined in normalized [0,1] coords against seeded lattices whose
# sizes depend only on frequency (never on `size`), so patch_mask(seed) yields
# the SAME shape at any resolution — the low-res coverage sample and the full-res
# baked visual agree. that scale-invariance is load-bearing; keep it.

import numpy as np

# shape defaults live in data/balance.json (headless-safe: balance pulls in only
# json + config, no pygame). callers can still override per-call via kwargs.
from balance import ROCK_OCTAVES, ROCK_WARP, ROCK_BASE_LEVEL, ROCK_EDGE_BIAS


def _smoothstep(t: np.ndarray) -> np.ndarray:
    return t * t * (3.0 - 2.0 * t)


def _value_noise(u: np.ndarray, v: np.ndarray, freq: int, rng) -> np.ndarray:
    # coherent value noise: random values on a (freq+2)^2 lattice, bilinearly
    # interpolated with a smoothstep fade. u, v are normalized coords in ~[0,1]
    # (may stray slightly outside when domain-warped, hence the +2 pad + clip).
    lat = rng.random((freq + 2, freq + 2), dtype=np.float32)
    gx, gy = u * freq, v * freq
    x0f, y0f = np.floor(gx), np.floor(gy)
    fx, fy = _smoothstep(gx - x0f), _smoothstep(gy - y0f)
    xi = np.clip(x0f.astype(np.int32), 0, freq)
    yi = np.clip(y0f.astype(np.int32), 0, freq)
    a = lat[yi, xi];       b = lat[yi, xi + 1]
    c = lat[yi + 1, xi];   d = lat[yi + 1, xi + 1]
    return (a * (1 - fx) + b * fx) * (1 - fy) + (c * (1 - fx) + d * fx) * fy


def _fbm(u: np.ndarray, v: np.ndarray, rng, octaves: int, freq0: float,
         gain: float = 0.5, lac: float = 2.0) -> np.ndarray:
    # fractional Brownian motion: octaves of value noise at rising frequency and
    # falling amplitude. normalized by the FIXED infinite-octave sum 1/(1-gain),
    # NOT the actual per-call sum — so dropping the high octaves (cheap coverage
    # pass) leaves the low-octave values untouched. that keeps the gross shape
    # identical to the full-octave visual; only the fine fray differs.
    total = np.zeros(u.shape, np.float32)
    amp, freq = 1.0, float(freq0)
    for _ in range(octaves):
        total += amp * _value_noise(u, v, int(freq), rng)
        amp *= gain
        freq *= lac
    return total * (1.0 - gain)


def patch_mask(size: int, seed: int, *, octaves: int = ROCK_OCTAVES, warp: float = ROCK_WARP,
               base_level: float = ROCK_BASE_LEVEL, edge_bias: float = ROCK_EDGE_BIAS) -> np.ndarray:
    # returns a (size, size) uint8 array, 255 = rock, 0 = grass. tuning knobs:
    #   octaves    — noise detail; more = finer frays (and a bit more cost).
    #   warp       — domain-warp strength; higher = more twisted, less blobby.
    #   base_level — noise cutoff at the center; lower = the patch fills more.
    #   edge_bias  — how hard the radial falloff bites; higher = tighter blob
    #                that frays sooner toward the edges.
    rng = np.random.default_rng(seed)
    v, u = np.mgrid[0:size, 0:size].astype(np.float32) / np.float32(size)

    # domain warp: bend the sample grid by a low-frequency fBm (drawn first so
    # the rng sequence — and thus the shape — is identical at every resolution).
    du = _fbm(u, v, rng, octaves=2, freq0=2) - 0.5
    dv = _fbm(u, v, rng, octaves=2, freq0=2) - 0.5
    field = _fbm(u + warp * du, v + warp * dv, rng, octaves=octaves, freq0=3)

    # radial falloff, 0 at center -> ~1 at the mid-edges, so the threshold rises
    # outward and the blob frays into grass instead of hitting the bbox.
    d = np.sqrt((u - 0.5) ** 2 + (v - 0.5) ** 2) * 2.0
    return (field > base_level + edge_bias * d).astype(np.uint8) * 255


def tile_coverage(mask: np.ndarray, tile: int) -> np.ndarray:
    # block-average the mask down to one value per tile: the fraction (0..1) of
    # that tile covered by rock. same reshape trick as render._downsample_rgb.
    # mask side length is an exact multiple of `tile`, so the reshape is clean.
    h, w = mask.shape
    ny, nx = h // tile, w // tile
    block = mask[:ny * tile, :nx * tile].reshape(ny, tile, nx, tile)
    return block.mean(axis=(1, 3)) / 255.0
