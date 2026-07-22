# Current-direct visual goldens

These two source-only rasters mirror the anonymous Singapore/USD `1365x900`
source captures documented on 2026-07-20. The original capture files have
legacy `.png` names but JPEG byte encoding; their bytes and frozen SHA-256
digests are intentionally retained rather than transcoded after capture.
Chromium's evidenced content raster is `1350x890` after browser chrome and
scrollbars. They are immutable release inputs for the two `current-direct`
visual checkpoints; they are not runtime assets and do not increase the 452
source-asset-record or 454 runtime-mapping denominators.

- `desktop-home-1365x900.png` — SHA-256
  `94b11aecf9f320d9e9b6a8308a70b81d709be50096fd4d6ef1b41b35a8902110`
- `desktop-search-portable-ssd-1365x900.png` — SHA-256
  `d47bb91224738f256f4f2398bea812b609f71559b5a2c570e91761d0d06d07fd`

Release visual evidence copies these files into the attempt-owned artifact
root, captures the clone at the same configured viewport, emits amplified PNG
diffs, and binds every raw file by byte count and SHA-256.
