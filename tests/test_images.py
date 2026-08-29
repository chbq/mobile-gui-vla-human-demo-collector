import unittest
from io import BytesIO

from PIL import Image

from mobile_gui_vla_data_lab.images import (
    mean_absolute_delta,
    preview_jpeg,
    visual_signature,
)


def png_bytes(color):
    image = Image.new("RGB", (1080, 2400), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


class ImageTests(unittest.TestCase):
    def test_preview_is_small_jpeg_with_preserved_aspect_ratio(self):
        source = png_bytes((20, 40, 80))
        rendered = preview_jpeg(source)
        self.assertLess(len(rendered), len(source))
        with Image.open(BytesIO(rendered)) as image:
            self.assertEqual(image.format, "JPEG")
            self.assertEqual(image.size, (432, 960))

    def test_visual_delta_distinguishes_stable_and_changed_frames(self):
        dark = visual_signature(png_bytes((10, 10, 10)))
        same = visual_signature(png_bytes((10, 10, 10)))
        light = visual_signature(png_bytes((240, 240, 240)))
        self.assertEqual(mean_absolute_delta(dark, same), 0.0)
        self.assertGreater(mean_absolute_delta(dark, light), 200.0)


if __name__ == "__main__":
    unittest.main()
