# T-shirt mockup and asset references

These assets support visual mockups. They are not evidence of the manufactured shirt and must not be presented as actual product photography.

## Bundled file

- `sources/tshirt-front-pexels-mockupbee-12039633.jpg` — 3000 × 2000 JPEG. Used as an explicitly labeled generic front mockup fallback in Photo View when a T-shirt has neither an uploaded front photo nor a primary product image. Customer artwork is composited into an estimated print region, but the placement is not verified against a physical garment.

## Source and license

- Pexels: [White T-Shirt on White Surface](https://www.pexels.com/photo/white-t-shirt-on-white-surface-12039633/), creator mockupbee. Downloaded from the photo's official Pexels download link. The Pexels License allows website and e-commerce use without required attribution. Attribution is retained here as provenance. The source page describes the image as a mockup. See [Pexels License](https://www.pexels.com/license/).
- Pixabay: [T-Shirt, Clothes, Blouse](https://pixabay.com/illustrations/t-shirt-clothes-blouse-3634762/) is a single collage illustration with three shirt angles. It was inspected during sourcing but is not retained or served by the project. It must not be split or represented as separate photographs. The contributor name was not available in the page content inspected. The Pixabay Content License allows commercial use subject to prohibited-use and standalone redistribution restrictions; attribution is not required. See [Pixabay Content License](https://pixabay.com/service/license-summary/).

## Photo composition and admin configuration

Photo View uses the same design-layer state as the 3D view, including image layers and transparent PNG text layers. It composites only layers assigned to the selected photo view and print location. The artwork is clipped to a normalized quadrilateral, then warped using a subdivided projective mapping. Treat the result as an approximate mockup, not a production placement proof.

In Admin → Products, configure **Photo print boundaries (normalized JSON)** per product. Coordinates range from 0 to 1 relative to the photo's top-left and must describe four convex corners in order. Example:

```json
{
  "front": {
    "front_center": [[0.37, 0.33], [0.63, 0.33], [0.63, 0.57], [0.37, 0.57]],
    "left_chest": [[0.44, 0.34], [0.56, 0.34], [0.56, 0.46], [0.44, 0.46]]
  },
  "back": {
    "back_center": [[0.36, 0.31], [0.64, 0.31], [0.64, 0.63], [0.36, 0.63]]
  }
}
```

Upload actual front, back, left, right, and detail photos through the existing product photo controls. Configure corners for each photo view and supported print location. Missing photo views remain missing; the generic Pexels fallback only represents a front mockup.
