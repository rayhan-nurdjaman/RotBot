import base64
import io

from PIL import Image

import assistant as Assistant
from instance import Instance


def image_bytes(size, fmt='JPEG', mode='RGB', **kw):
    buffer = io.BytesIO()
    Image.new(mode, size, 'white').save(buffer, format=fmt, **kw)
    return buffer.getvalue()


def decode(data_url):
    prefix = 'data:image/jpeg;base64,'
    assert data_url.startswith(prefix)
    data = base64.b64decode(data_url[len(prefix):])
    return data, Image.open(io.BytesIO(data))


def test_a_jpeg_that_fits_is_sent_unchanged():
    original = image_bytes((1280, 960), quality=90)

    data, image = decode(Assistant.encode_image(original))

    assert data == original
    assert image.size == (1280, 960)


def test_a_large_image_is_scaled_to_the_long_edge_cap_keeping_its_shape():
    data, image = decode(Assistant.encode_image(image_bytes((3200, 2400))))

    assert image.format == 'JPEG'
    assert image.size == (Assistant.IMAGE_MAX_EDGE, Assistant.IMAGE_MAX_EDGE * 3 // 4)


def test_a_tall_image_is_capped_on_its_height():
    data, image = decode(Assistant.encode_image(image_bytes((1000, 3000))))

    assert image.size[1] == Assistant.IMAGE_MAX_EDGE
    assert image.size[0] < 1000


def test_a_small_image_is_not_enlarged():
    data, image = decode(Assistant.encode_image(image_bytes((300, 200), fmt='PNG')))

    assert image.size == (300, 200)


def test_a_png_with_transparency_becomes_a_jpeg():
    data, image = decode(Assistant.encode_image(image_bytes((200, 100), fmt='PNG', mode='RGBA')))

    assert image.format == 'JPEG'


def test_the_stored_image_part_has_no_detail_field():
    instance = Instance('chat-1')
    instance.add_user_message('alice: look', 'data:image/jpeg;base64,AAAA')

    part = instance.context_window[0]['content'][1]

    assert part == {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,AAAA'}}
