import unittest

from mobile_gui_vla_data_lab.coordinates import (
    ImageViewport,
    map_drag,
    map_pointer,
    rendered_image_rect,
)


class CoordinateTests(unittest.TestCase):
    def test_center_and_near_corners_at_two_display_scales(self):
        for viewport in (ImageViewport(0, 0, 540, 1200), ImageViewport(5, 7, 270, 600)):
            center = map_pointer(
                pointer_x=viewport.x + viewport.width / 2,
                pointer_y=viewport.y + viewport.height / 2,
                viewport=viewport,
                frame_width_px=1080,
                frame_height_px=2400,
                orientation=0,
            )
            self.assertEqual((center.x_px, center.y_px), (540, 1200))
            points = (
                (viewport.x, viewport.y, 0, 0),
                (viewport.x + viewport.width - 0.01, viewport.y, 1079, 0),
                (viewport.x, viewport.y + viewport.height - 0.01, 0, 2399),
                (
                    viewport.x + viewport.width - 0.01,
                    viewport.y + viewport.height - 0.01,
                    1079,
                    2399,
                ),
            )
            for x, y, expected_x, expected_y in points:
                mapped = map_pointer(
                    pointer_x=x,
                    pointer_y=y,
                    viewport=viewport,
                    frame_width_px=1080,
                    frame_height_px=2400,
                    orientation=0,
                )
                self.assertEqual((mapped.x_px, mapped.y_px), (expected_x, expected_y))

    def test_letterbox_is_explicit_and_padding_is_rejected(self):
        viewport = ImageViewport(0, 0, 600, 600)
        image = rendered_image_rect(1080, 2400, viewport)
        self.assertAlmostEqual(image.width, 270)
        self.assertAlmostEqual(image.x, 165)
        with self.assertRaisesRegex(ValueError, "outside"):
            map_pointer(
                pointer_x=100,
                pointer_y=300,
                viewport=viewport,
                frame_width_px=1080,
                frame_height_px=2400,
                orientation=0,
            )

    def test_drag_and_orientation_provenance(self):
        start, end = map_drag(
            start_x=10,
            start_y=20,
            end_x=90,
            end_y=180,
            viewport=ImageViewport(0, 0, 100, 200),
            frame_width_px=1000,
            frame_height_px=2000,
            orientation=1,
        )
        self.assertEqual((start.x_px, start.y_px), (100, 200))
        self.assertEqual((end.x_px, end.y_px), (900, 1800))
        self.assertEqual(start.provenance()["original_frame"]["orientation"], 1)


if __name__ == "__main__":
    unittest.main()
