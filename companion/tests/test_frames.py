import os
import random

from PIL import Image

from foreverai.frames import FrameAssembler, decode_frame, encode_frames


def _roundtrip(data: bytes, cell: int) -> bytes:
    images = encode_frames(data, msg_id=7, cell=cell)
    asm = FrameAssembler()
    out = None
    for img in images:
        frame = decode_frame(img)
        assert frame is not None
        assert frame.msg_id == 7
        assert frame.cell == cell
        out = asm.add(frame) or out
    return out


def test_roundtrip_multi_frame_cell1():
    data = os.urandom(50000)
    assert _roundtrip(data, 1) == data


def test_roundtrip_cell2_and_cell4():
    data = bytes(random.Random(1).randrange(256) for _ in range(3000))
    assert _roundtrip(data, 2) == data
    assert _roundtrip(data, 4) == data


def test_frames_split_and_count():
    data = os.urandom(50000)
    images = encode_frames(data, msg_id=1, cell=1)
    frames = [decode_frame(i) for i in images]
    assert len(frames) == 3  # 128*128*3/2 - 18 = 24558 bytes per frame (12 bits per cell)
    assert [f.index for f in frames] == [0, 1, 2]
    assert all(f.count == 3 for f in frames)


def test_out_of_order_frames():
    data = os.urandom(60000)
    images = encode_frames(data, msg_id=9, cell=1)
    asm = FrameAssembler()
    frames = [decode_frame(i) for i in images]
    assert len(frames) == 3
    assert asm.add(frames[2]) is None and asm.add(frames[1]) is None
    assert asm.add(frames[0]) == data


def test_plain_screenshot_is_not_a_frame():
    img = Image.new("RGB", (1920, 1080), (30, 80, 20))
    assert decode_frame(img) is None


def test_frame_on_larger_screenshot_background():
    data = b"hello forever" * 100
    frame_img = encode_frames(data, msg_id=3, cell=1)[0]
    screen = Image.new("RGB", (1920, 1080), (12, 40, 9))
    screen.paste(frame_img, (0, 0))
    frame = decode_frame(screen)
    assert frame is not None and frame.payload == data
