# README media sources

The PNGs are unchanged captures of the real Tk application, produced during the 1.0.17 UI verification on 2026-10-28. They use an in-memory demo configuration. They are not AI-generated screens or evidence of an end-to-end dictation; the visible settings and version labels have not been retouched. The current release is 1.0.19.

The source captures were saved in the local `.work/combo-probe/shots2/` verification workspace, with `home_probe.py` using the application's `ControlPanel` and `TrayFlyout` classes. That local workspace is not part of the public repository. Only the three reviewed captures listed below are published; no transcript history or Connections/API-key screen is included.

| Published file | Original capture | SHA-256 (unchanged bytes) |
| --- | --- | --- |
| `quick-controls-light.png` | `en_light_flyout.png` | `508044b414d474d7fa3a78639986b545a77c0d8480d3d49341e582d7f2a1f375` |
| `quick-controls-dark.png` | `it_dark_flyout.png` | `353dddcb59643aeddd53703072585d5d096a84221f3e6e652c6572d5a76176ad` |
| `dictation-settings.png` | `en_light_dictation.png` | `d758c1584d5fe646de6d67a755b660394aa4fde1b4d57b29266a01d12ffde4d7` |

`hero.svg` is an editable vector title graphic, not a screenshot. `social-card.png` is its 1280×640 raster export, rendered with ImageMagick for sharing. Its typography and warm accent accompany the application imagery; it does not replace the application's marble-sphere icon. All fonts use system fallbacks, with no external resources or scripts.

To refresh the gallery, capture the actual app with non-sensitive demo settings, preserve its pixels and version label, review the image for private data, then update this manifest and the screenshot note in the README. Do not substitute a mock interface for a real product recording.
