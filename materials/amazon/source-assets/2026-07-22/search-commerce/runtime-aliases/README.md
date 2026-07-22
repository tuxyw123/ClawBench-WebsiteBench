# Required runtime aliases

These two dated source-side mirrors close required runtime aliases that are not
represented by a one-to-one path in the original 452 source-asset records.
They are additional logical runtime declarations, not two new current source
captures.

- `nav-sprite.png` is byte-identical to the current-direct base asset
  `source-assets/2026-07-21/home/a131eec97c81ff48.png` (SHA-256
  `e08f4251ef6d42286a0dce6a79efa3316ff1b429f2f91cd815a17d9273cae1a1`).
  The extra record declares the different runtime URL actually referenced by
  `clone/static/styles.css`.
- `samsung-t7-main-historical.jpg` is byte-identical to the retained historical
  capture object
  `source-capture/objects/8c/8cc04703e91f9ecd787726f8b8fbc896b93a73ef87d71a4486e6afad0ad6c1a3`
  and the runtime path used by the frozen task fixture. Its evidence kind stays
  `historical`; the storage mirror does not upgrade it to current-direct.

Together with the original 452 current/bounded source records, these aliases
produce 454 required logical runtime records. The runtime asset tree contains
456 physical files: `samsung-t9.jpg` and `sandisk-extreme.jpg` are retained but
unreferenced legacy files and remain outside required closure.
