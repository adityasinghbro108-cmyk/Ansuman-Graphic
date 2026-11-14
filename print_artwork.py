"""Build print-ready artwork from immutable order design snapshots."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


# Calibrated printable areas in inches, with separate resolution for large format.
# Keep the ratios aligned with the region textures in static/design-3d.js.
PRINT_SPECS = {
    "tshirt": {"front_center": (12, 13.2, 300), "back_center": (12, 13.2, 300),
               "left_chest": (4.2, 4.5, 300), "right_chest": (4.2, 4.5, 300),
               "left_sleeve": (3.6, 4.8, 300), "right_sleeve": (3.6, 4.8, 300)},
    "mug": {"wrap": (8, 4, 300)},
    "pillow": {"front": (12, 10.17, 300)},
    "banner": {"full_front": (24, 12.42, 150)},
    "frame": {"photo": (7, 9, 300)},
    "phonecase": {"back": (4.4, 6, 300)},
    "poster": {"full_front": (12, 15.7, 300)},
    "gift": {"front": (12, 11.34, 300)},
}
CUSTOM_PRODUCT_PRINT_SPEC = (12, 12, 300)


class MissingPrintAsset(FileNotFoundError):
    """An order snapshot references an original artwork file that is unavailable."""


def _text_font(spec: dict, size: int):
    family = spec.get("font", "Arial")
    bold = bool(spec.get("bold"))
    italic = bool(spec.get("italic"))
    names = {
        "Arial": ["arialbi.ttf" if bold and italic else "ariali.ttf" if italic else "arialbd.ttf" if bold else "arial.ttf", "DejaVuSans-BoldOblique.ttf" if bold and italic else "DejaVuSans-Oblique.ttf" if italic else "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "LiberationSans-BoldItalic.ttf" if bold and italic else "LiberationSans-Italic.ttf" if italic else "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"],
        "Georgia": ["georgiaz.ttf" if bold and italic else "georgiai.ttf" if italic else "georgiab.ttf" if bold else "georgia.ttf", "DejaVuSerif-BoldItalic.ttf" if bold and italic else "DejaVuSerif-Italic.ttf" if italic else "DejaVuSerif-Bold.ttf" if bold else "DejaVuSerif.ttf", "LiberationSerif-BoldItalic.ttf" if bold and italic else "LiberationSerif-Italic.ttf" if italic else "LiberationSerif-Bold.ttf" if bold else "LiberationSerif-Regular.ttf"],
        "Impact": ["impact.ttf", "DejaVuSansCondensed-Bold.ttf", "LiberationSans-Bold.ttf"],
        "Trebuchet MS": ["trebucbi.ttf" if bold and italic else "trebucit.ttf" if italic else "trebucbd.ttf" if bold else "trebuc.ttf", "DejaVuSans-BoldOblique.ttf" if bold and italic else "DejaVuSans-Oblique.ttf" if italic else "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", "LiberationSans-BoldItalic.ttf" if bold and italic else "LiberationSans-Italic.ttf" if italic else "LiberationSans-Bold.ttf" if bold else "LiberationSans-Regular.ttf"],
    }.get(family, ["DejaVuSans.ttf"])
    windows_fonts = Path("C:/Windows/Fonts")
    for name in names:
        for candidate in (windows_fonts / name, Path("/usr/share/fonts/truetype/dejavu") / name,
                          Path("/usr/share/fonts/truetype/liberation2") / name):
            if candidate.is_file():
                try:
                    return ImageFont.truetype(str(candidate), size=size)
                except OSError:
                    pass
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_text_layer(spec: dict, width: int, height: int) -> Image.Image:
    text = spec.get("text")
    if not isinstance(text, str) or not text.strip():
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    base_ratio = 1600 / 700  # Match the transparent text layer created by the studio.
    if width / height > base_ratio:
        layer_h = height
        layer_w = max(1, round(height * base_ratio))
    else:
        layer_w = width
        layer_h = max(1, round(width / base_ratio))
    layer = Image.new("RGBA", (layer_w, layer_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    try:
        base_size = max(30, min(180, float(spec.get("size", 100))))
    except (ValueError, TypeError):
        base_size = 100
    font_size = max(1, round(base_size * 1.5 * layer_w / 1600))
    font = _text_font(spec, font_size)
    max_text_width = layer_w * 1450 / 1600
    while font_size > 1 and draw.textbbox((0, 0), text, font=font)[2] > max_text_width:
        font_size = max(1, font_size - max(1, round(6 * layer_w / 1600)))
        font = _text_font(spec, font_size)
    colors = {"black": (17,17,17,255), "white": (255,255,255,255), "red": (214,40,57,255),
              "blue": (36,99,197,255), "navy": (23,36,59,255), "purple": (113,57,189,255),
              "green": (22,130,76,255)}
    fill = colors.get(spec.get("color"), colors["navy"])
    center = (layer.width // 2, layer.height // 2)
    if layer_w * layer_h > 0:
        mask = Image.new("L", layer.size, 0)
        ImageDraw.Draw(mask).text(center, text, font=font, fill=255, anchor="mm", align=spec.get("alignment", "center"))
        shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        shadow.putalpha(mask.filter(ImageFilter.GaussianBlur(max(1, round(8 * layer_w / 1600)))).point(lambda value: round(value * .16)))
        layer.alpha_composite(shadow)
        draw = ImageDraw.Draw(layer)
        draw.text(center, text, font=font, fill=fill, anchor="mm", align=spec.get("alignment", "center"))
        mask.close(); shadow.close()
    return layer


def artwork_filename(artwork: dict) -> str:
    name = artwork.get("image")
    if not isinstance(name, str) or Path(name).name != name or name in {"", ".", ".."}:
        raise MissingPrintAsset("The original artwork file is missing.")
    return name


def group_artworks(item: dict) -> dict[str, list[dict]]:
    """Group immutable layers into independently printable surfaces."""
    design = item.get("design") if isinstance(item.get("design"), dict) else {}
    result: dict[str, list[dict]] = {}
    for layer in item.get("artworks", []):
        if not isinstance(layer, dict):
            continue
        location = layer.get("location") or design.get("default_location")
        if not isinstance(location, str):
            continue
        view = layer.get("view") or design.get("view") or "front"
        result.setdefault(f"{location}::{view}", []).append(layer)
    return result


def print_spec(item: dict, location: str) -> tuple[int, int, int]:
    slug = item.get("slug")
    spec = PRINT_SPECS.get(slug, {}).get(location)
    if spec is None and slug not in PRINT_SPECS and location == "front":
        spec = CUSTOM_PRODUCT_PRINT_SPEC
    if spec is None:
        raise ValueError("No print specification is configured for this product surface.")
    width_in, height_in, dpi = spec
    return max(1, round(width_in * dpi)), max(1, round(height_in * dpi)), dpi


def render_print_png(item: dict, location: str, upload_dir: Path, view: str | None = None) -> tuple[bytes, dict]:
    """Render one transparent print surface from the original image layers."""
    groups = group_artworks(item)
    view = view or "front"
    group_key = f"{location}::{view}"
    if group_key not in groups:
        raise ValueError("This order has no artwork for that print surface.")
    width, height, dpi = print_spec(item, location)
    if width * height > 45_000_000:
        raise ValueError("The configured print area exceeds the safe export size.")
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    design = item.get("design") if isinstance(item.get("design"), dict) else {}
    for layer in groups[group_key]:
        text_spec = layer.get("text_spec")
        if isinstance(text_spec, dict):
            source = _render_text_layer(text_spec, width, height)
        else:
            filename = artwork_filename(layer)
            source_path = (upload_dir / filename).resolve()
            if source_path.parent != upload_dir.resolve() or not source_path.is_file():
                canvas.close()
                raise MissingPrintAsset(f"Original artwork is unavailable: {filename}")
            try:
                with Image.open(source_path) as opened:
                    source = ImageOps.exif_transpose(opened).convert("RGBA")
            except (OSError, ValueError) as error:
                canvas.close()
                raise MissingPrintAsset(f"Original artwork cannot be decoded: {filename}") from error
        try:
            scale = layer.get("scale", 1)
            try:
                scale = float(scale)
            except (ValueError, TypeError):
                scale = 1
            scale = min(3.0, max(.3, scale))
            # The design studio's product UV canvases fit each layer inside an
            # 82% safe box, preserving the original image aspect ratio.
            box_w, box_h = max(1, round(width * .82 * scale)), max(1, round(height * .82 * scale))
            fitted = ImageOps.contain(source, (box_w, box_h), method=Image.Resampling.LANCZOS)
            source.close()
            rotation = layer.get("rotation", 0)
            try:
                rotation = min(180, max(-180, float(rotation)))
            except (ValueError, TypeError):
                rotation = 0
            if rotation:
                rotated = fitted.rotate(-rotation, resample=Image.Resampling.BICUBIC, expand=True)
                fitted.close()
                fitted = rotated
            try:
                x = min(.5, max(-.5, float(layer.get("position_x", 0))))
                y = min(.5, max(-.5, float(layer.get("position_y", 0))))
            except (ValueError, TypeError):
                x = y = 0
            center_x = round(width * (.5 + x))
            center_y = round(height * (.5 + y))
            canvas.alpha_composite(fitted, (center_x - fitted.width // 2, center_y - fitted.height // 2))
            fitted.close()
        finally:
            source.close()
    buffer = io.BytesIO()
    canvas.save(buffer, "PNG", dpi=(dpi, dpi), optimize=True)
    canvas.close()
    return buffer.getvalue(), {"width": width, "height": height, "dpi": dpi, "location": location}
