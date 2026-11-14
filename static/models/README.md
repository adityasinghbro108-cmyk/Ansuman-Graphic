# Product model integration

No licensed product GLB assets are installed. Until an inspected asset is added, the studio labels its current procedural previews as stylized and keeps them as the fallback.

## Candidate T-shirt model (not downloaded or integrated)

- Title: Men Regular Apparel Fit Sporty T-Shirt
- Creator: BINARYCLOTH
- Listing: https://sketchfab.com/3d-models/men-regular-apparel-fit-sporty-t-shirt-4d055bb8c1e04549a4b2dac7b27ebb2c
- Listing license shown in the available listing metadata: **CC Attribution (Creative Commons Attribution)**. The direct page could not be opened in this environment. Verify the license on the listing at download time and retain its license/attribution record with the asset. Follow Sketchfab's attribution requirements wherever the model is used or adapted.
- The listing describes GLB, a full UV unwrap, one material, and fabric textures. These are listing claims only; this project's copy has not been downloaded, inspected, or tested, so print suitability and mapped print regions are unknown.
- Manual download: open the listing above and use its normal **Download 3D Model** control. Sign in if Sketchfab requires it; do not use third-party downloaders or alternate endpoints. If GLB is offered, save it as `static/models/tshirt-binarycloth-4d055bb8c1e04549a4b2dac7b27ebb2c.glb`. Preserve any accompanying license/attribution notice. If the listing or download flow does not confirm the same commercial-use-compatible CC Attribution terms, do not use the model.
- The Fab alternative is not approved for use: its listing reports “License terms: -”.

## Adding an inspected model

For each product, add a commercially licensed, UV-mapped `.glb` under `static/models/` and update that product's `model` and `license` entries in `manifest.json`. Each mapped print region must be a separate mesh using the exact configured name (for example `PRINT_FRONT` or `PRINT_LEFT_SLEEVE`), have UVs authored for the printable region, and use a neutral printable base material. Preserve all other PBR materials, textures and product meshes in the GLB. Record the license and attribution URL in the `license` value.

The Three.js loader checks that every configured print mesh exists before replacing the stylized fallback. It then maps the studio's per-region, transparent artwork textures to those UV-mapped surfaces. Invalid, missing or incomplete assets leave the working fallback in place and show a status notice.
